# app/services/rag_service.py
"""
RAG (Retrieval-Augmented Generation) - lõi retrieval cho tính năng /ask.

/ask CHỈ dùng RAG (không còn text-to-SQL - AI tự sinh SQL tự do kém ổn
định, hay lỗi cú pháp/hallucinate tên cột, đặc biệt với model free; xem
lịch sử quyết định trong nl_query_service.py). Câu hỏi được embed thành
vector, so khớp cosine similarity với embedding của từng repair để tìm các
bản ghi GẦN NGHĨA nhất - bắt được lỗi tương tự dù cách diễn đạt khác nhau
(vd "chập nguồn do ẩm" và "board vào nước gây ngắn mạch"), điều mà so khớp
từ khoá (LIKE) không làm được.

Lưu trữ: mỗi Repair có tối đa 1 dòng trong bảng `repair_embeddings` (xem
app/models/repair_embedding.py), vector lưu dạng JSON. So khớp cosine
similarity tính trực tiếp bằng Python - đủ nhanh cho quy mô vài nghìn repair
của hệ thống này, không cần thêm vector DB (FAISS/pgvector/...).
"""
import math
from typing import Callable, List, Optional, Tuple

from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
# Board/Contractor không được dùng trực tiếp trong file này, nhưng BẮT BUỘC
# phải import ở đây: Repair khai báo relationship("Board", ...)/("Contractor",
# ...) bằng TÊN CLASS (string) - SQLAlchemy chỉ resolve được tên đó nếu class
# tương ứng đã được import (đăng ký vào registry) từ trước. Trong bot chính,
# router.py tình cờ import Board/Contractor trước khi dùng tới Repair nên
# không lỗi; nhưng 1 script chạy riêng chỉ import rag_service (vd
# scripts/build_repair_embeddings.py) sẽ thiếu, gây lỗi
# "NoReferencedTableError: ... could not find table 'boards'/'contractors'"
# ngay khi query Repair lần đầu. Import ở đây để mọi entrypoint đều an toàn.
from app.models.board import Board  # noqa: F401
from app.models.contractor import Contractor  # noqa: F401
from app.models.repairs import Repair
from app.models.repair_embedding import RepairEmbedding
from app.services.embedding_service import get_embedding_service


def _build_repair_text(repair: Repair) -> str:
    """Văn bản đại diện cho 1 repair, dùng để embed. Vì RAG giờ là NGUỒN DỮ
    LIỆU DUY NHẤT của /ask (không còn SQL đối chiếu), đưa thêm vài field
    định danh (người sửa, ngày nhận, kết quả test) ngoài mô tả lỗi thuần -
    giúp các câu hỏi kiểu "user X đã làm gì" cũng có cơ hội khớp được, dù
    RAG vẫn chủ yếu mạnh cho câu hỏi về NỘI DUNG lỗi hơn là lọc chính xác."""
    creator = repair.created_by.full_name if getattr(repair, "created_by", None) else None
    fields = [
        ("Board", repair.board_code or repair.board_id),
        ("Người thực hiện", creator),
        ("Ngày nhận", repair.date_receive.isoformat() if repair.date_receive else None),
        ("Test tool result", repair.test_tool_result.value if repair.test_tool_result else None),
        ("Hiện tượng ban đầu", repair.original_phenomenon),
        ("Nguyên nhân lỗi", repair.failure_cause),
        ("Cách xử lý", repair.disposition),
        ("Kết quả sau sửa", repair.after_repair_result.value if repair.after_repair_result else None),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    return "\n".join(lines)


def reindex_repair(repair_id: int) -> None:
    """Tạo/cập nhật embedding cho 1 repair. Gọi mỗi khi repair được tạo mới
    hoặc sửa field liên quan (xem hook trong repair_command.py). Best-effort
    theo thiết kế của caller - hàm này TỰ RAISE nếu lỗi (vd thiếu thư viện
    sentence-transformers, model chưa tải được); caller nên tự bọc try/except
    vì đây là bước bổ trợ, không nên làm hỏng luồng chính (tạo/sửa repair)."""
    with SessionLocal() as db:
        repair = (
            db.query(Repair)
            .options(joinedload(Repair.created_by))
            .filter(Repair.id == repair_id)
            .first()
        )
        if not repair:
            return

        text = _build_repair_text(repair)
        if not text.strip():
            # Không còn field nào để embed -> xoá embedding cũ (nếu có) để
            # không giữ lại vector đã lỗi thời.
            db.query(RepairEmbedding).filter(RepairEmbedding.repair_id == repair_id).delete()
            db.commit()
            return

        service = get_embedding_service()
        vector = service.embed([text], input_type="document")[0]

        row = db.query(RepairEmbedding).filter(RepairEmbedding.repair_id == repair_id).first()
        if row is None:
            row = RepairEmbedding(repair_id=repair_id)
            db.add(row)
        row.source_text = text
        row.embedding_model = service.model
        row.embedding = vector
        db.commit()


def reindex_all(progress_callback: Optional[Callable[[int, int], None]] = None) -> int:
    """Backfill embedding cho TẤT CẢ repair hiện có - dùng bởi
    scripts/build_repair_embeddings.py (chạy 1 lần qua terminal khi mới bật
    tính năng RAG, hoặc sau khi đổi model embedding). Trả về số lượng đã xử
    lý. Không bọc try/except ở đây - để script terminal tự quyết định cách
    báo lỗi (dừng lại hay bỏ qua tiếp)."""
    with SessionLocal() as db:
        repair_ids = [row[0] for row in db.query(Repair.id).order_by(Repair.id).all()]

    for i, repair_id in enumerate(repair_ids, start=1):
        reindex_repair(repair_id)
        if progress_callback:
            progress_callback(i, len(repair_ids))
    return len(repair_ids)


def search_similar(query_text: str, top_k: int = 30) -> List[Tuple[Repair, float]]:
    """Tìm top_k repair có nội dung GẦN NGHĨA nhất với query_text. Trả về
    list rỗng nếu chưa có repair nào được index, hoặc nếu model embedding
    local lỗi (vd chưa cài sentence-transformers, chưa tải được model) -
    KHÔNG raise, để nl_query_service báo lỗi rõ ràng cho Admin thay vì crash."""
    query_text = (query_text or "").strip()
    if not query_text:
        return []

    try:
        service = get_embedding_service()
        query_vector = service.embed([query_text], input_type="query")[0]
    except Exception:
        return []

    with SessionLocal() as db:
        rows = (
            db.query(RepairEmbedding)
            .options(joinedload(RepairEmbedding.repair).joinedload(Repair.created_by))
            .all()
        )
        scored = [
            (row.repair, _cosine_similarity(query_vector, row.embedding))
            for row in rows if row.repair is not None
        ]

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
