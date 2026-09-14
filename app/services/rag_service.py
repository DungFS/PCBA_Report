# app/services/rag_service.py
"""
RAG (Retrieval-Augmented Generation) cho tính năng /ask - tìm các repair có
mô tả lỗi TƯƠNG TỰ NHAU theo NGỮ NGHĨA (không chỉ khớp từ khoá như LIKE
trong nl_query_service.py), dùng vector embedding sinh LOCAL (xem
app/services/embedding_service.py).

Khác biệt với nl_query_service.py:
    - nl_query_service.py : retrieval bằng SQL do AI sinh ra - CHÍNH XÁC cho
      câu hỏi đếm/liệt kê/lọc theo điều kiện rõ ràng (vd "user A sửa được
      bao nhiêu board"), nhưng yếu khi 2 lỗi diễn đạt khác từ mà cùng bản
      chất (LIKE không bắt được).
    - rag_service.py      : retrieval bằng cosine similarity giữa các vector
      embedding - bắt được lỗi tương tự dù cách diễn đạt khác nhau (vd "chập
      nguồn do ẩm" và "board vào nước gây ngắn mạch").

/ask (xem nl_query_service.answer_question) dùng CẢ HAI, đưa kết quả của cả
2 nguồn cho AI ở bước tổng hợp câu trả lời cuối cùng.

Lưu trữ: mỗi Repair có tối đa 1 dòng trong bảng `repair_embeddings` (xem
app/models/repair_embedding.py), vector lưu dạng JSON. So khớp cosine
similarity tính trực tiếp bằng Python - đủ nhanh cho quy mô vài nghìn repair
của hệ thống này, không cần thêm vector DB (FAISS/pgvector/...).
"""
import math
from typing import Callable, List, Optional, Tuple

from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models.repairs import Repair
from app.models.repair_embedding import RepairEmbedding
from app.services.embedding_service import get_embedding_service


def _build_repair_text(repair: Repair) -> str:
    """Chỉ lấy các field liên quan tới MÔ TẢ LỖI - ảnh, ticket_id, ngày
    tháng... không giúp ích cho so khớp ngữ nghĩa, chỉ làm loãng vector."""
    fields = [
        ("Board", repair.board_code or repair.board_id),
        ("Hiện tượng ban đầu", repair.original_phenomenon),
        ("Nguyên nhân lỗi", repair.failure_cause),
        ("Cách xử lý", repair.disposition),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    return "\n".join(lines)


def reindex_repair(repair_id: int) -> None:
    """Tạo/cập nhật embedding cho 1 repair. Gọi mỗi khi repair được tạo mới
    hoặc sửa field mô tả lỗi (xem hook trong repair_command.py). Best-effort
    theo thiết kế của caller - hàm này TỰ RAISE nếu lỗi (vd thiếu thư viện
    sentence-transformers, model chưa tải được); caller nên tự bọc try/except
    vì đây là bước bổ trợ, không nên làm hỏng luồng chính (tạo/sửa repair)."""
    with SessionLocal() as db:
        repair = db.query(Repair).filter(Repair.id == repair_id).first()
        if not repair:
            return

        text = _build_repair_text(repair)
        if not text.strip():
            # Không còn field mô tả lỗi nào -> xoá embedding cũ (nếu có) để
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


def search_similar(query_text: str, top_k: int = 10) -> List[Tuple[Repair, float]]:
    """Tìm top_k repair có mô tả lỗi GẦN NGHĨA nhất với query_text. Trả về
    list rỗng nếu chưa có repair nào được index, hoặc nếu model embedding
    local lỗi (vd chưa cài sentence-transformers, chưa tải được model) -
    KHÔNG raise, vì đây là bước bổ sung cho /ask, lỗi ở đây không nên làm
    hỏng cả câu trả lời (nl_query_service vẫn có kết quả SQL)."""
    query_text = (query_text or "").strip()
    if not query_text:
        return []

    try:
        service = get_embedding_service()
        query_vector = service.embed([query_text], input_type="query")[0]
    except Exception:
        return []

    with SessionLocal() as db:
        rows = db.query(RepairEmbedding).options(joinedload(RepairEmbedding.repair)).all()
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
