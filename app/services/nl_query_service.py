# app/services/nl_query_service.py
"""
Service cho tính năng /ask - hỏi đáp AI về dữ liệu repair (chỉ Admin).

CHỈ dùng RAG (semantic search bằng vector embedding, xem rag_service.py) -
KHÔNG còn text-to-SQL. Bản đầu tiên của /ask để AI tự sinh câu SQL từ câu
hỏi tự nhiên rồi chạy trực tiếp, nhưng thực tế cho thấy bước này KHÔNG ổn
định (AI hay lỗi cú pháp SQL, hallucinate tên cột, đặc biệt với model free
qua OpenRouter) - đã bỏ hẳn để đơn giản hoá và tăng độ tin cậy.

Luồng hiện tại: câu hỏi -> tìm top-K repair có nội dung GẦN NGHĨA nhất
(rag_service.search_similar) -> đưa các bản ghi đó cho AI tổng hợp câu trả
lời tự nhiên bằng tiếng Việt.

GIỚI HẠN CẦN BIẾT: vì không còn truy vấn SQL đếm/lọc chính xác, các câu hỏi
dạng ĐẾM/TỔNG HỢP (vd "user A đã sửa được BAO NHIÊU board") chỉ mang tính
GẦN ĐÚNG - AI chỉ đếm được trên top-K bản ghi lấy về (xem RAG_TOP_K), không
phải quét toàn bộ DB. /ask phù hợp nhất cho: tìm lỗi tương tự, đề xuất cách
sửa dựa trên lịch sử, tra cứu/tóm tắt nội dung - không phải công cụ báo cáo
số liệu chính xác (dùng /user_report, /repair_detail_report... cho việc đó).
"""
from dataclasses import dataclass
from typing import List, Tuple

from app.services.claude_service import get_claude_service
from app.services.rag_service import search_similar

# Tăng đáng kể so với hồi còn kết hợp SQL (khi đó RAG chỉ là nguồn bổ sung,
# top_k=10 là đủ) - giờ RAG là nguồn DUY NHẤT nên cần lấy rộng hơn để giảm
# bớt (không loại bỏ hoàn toàn được) hạn chế đếm/liệt kê không đầy đủ nói ở trên.
RAG_TOP_K = 30


class NLQueryError(Exception):
    """Lỗi có thể hiển thị trực tiếp cho người dùng (không phải lỗi hệ thống)."""


@dataclass
class AskResult:
    similar_count: int
    answer_text: str = ""


def answer_question(question: str) -> AskResult:
    """Hàm chính - gọi từ bot command. Ném NLQueryError nếu câu hỏi trống;
    ném Exception khác nếu lỗi kết nối AI. RAG tự nuốt lỗi của chính nó
    (xem rag_service.search_similar) nên answer_question không cần bọc
    thêm - trường hợp xấu nhất chỉ là similar rỗng, AI vẫn trả lời được
    (nói rõ không tìm thấy dữ liệu phù hợp)."""
    question = (question or "").strip()
    if not question:
        raise NLQueryError("Câu hỏi trống.")

    similar = search_similar(question, top_k=RAG_TOP_K)

    claude = get_claude_service()
    answer_text = claude.ask(
        prompt=_build_answer_prompt(question, similar),
        system=_ANSWER_SYSTEM_PROMPT,
        max_tokens=900,
        temperature=0.3,
    )

    return AskResult(similar_count=len(similar), answer_text=answer_text)


_ANSWER_SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích dữ liệu cho hệ thống quản lý sửa chữa board PCBA. "
    "Bạn sẽ nhận được câu hỏi gốc của Admin và danh sách các bản ghi repair "
    "GẦN NGHĨA NHẤT với câu hỏi (tìm bằng vector similarity - so khớp ngữ "
    "nghĩa, không phải khớp từ khoá chính xác), đã xếp hạng theo độ giống "
    "cao -> thấp. Hãy trả lời NGẮN GỌN, RÕ RÀNG bằng tiếng Việt, dựa HOÀN "
    "TOÀN vào dữ liệu được cung cấp - KHÔNG bịa thêm thông tin không có "
    "trong dữ liệu.\n"
    "- Nếu dữ liệu rỗng hoặc không có bản ghi nào thực sự liên quan (độ "
    "giống thấp): nói rõ không tìm thấy dữ liệu phù hợp, đừng cố suy diễn.\n"
    "- Nếu câu hỏi yêu cầu đếm/liệt kê (vd 'user X sửa được bao nhiêu "
    "board'): trả lời dựa trên SỐ BẢN GHI THẤY ĐƯỢC trong dữ liệu, nhưng "
    "PHẢI nói rõ đây là số đếm được trên các bản ghi liên quan nhất tìm "
    "thấy (có giới hạn số lượng), KHÔNG khẳng định là con số tuyệt đối/đầy "
    "đủ của toàn bộ hệ thống - gợi ý Admin dùng lệnh báo cáo (vd "
    "/user_report, /repair_detail_report) nếu cần số liệu chính xác 100%.\n"
    "- Nếu câu hỏi yêu cầu tìm lỗi tương tự nhau: liệt kê các bản ghi có độ "
    "giống cao nhất, chỉ ra điểm chung về hiện tượng/nguyên nhân.\n"
    "- Nếu câu hỏi yêu cầu đề xuất cách sửa chữa: dựa vào cột 'Cách xử lý' "
    "của các bản ghi tương tự (đặc biệt các bản ghi có Kết quả sau sửa = "
    "PASS) để đề xuất, nêu rõ đây là dựa theo lịch sử đã từng sửa tương tự, "
    "không phải suy đoán ngoài dữ liệu."
)


def _build_answer_prompt(question: str, similar: List[Tuple[object, float]]) -> str:
    if not similar:
        data_section = "(không tìm thấy bản ghi nào liên quan - có thể DB chưa được index, xem run_build_embeddings.sh)"
    else:
        lines = [_format_similar_repair(repair, score) for repair, score in similar]
        data_section = f"Tìm thấy {len(similar)} bản ghi liên quan (đã xếp hạng theo độ giống):\n" + "\n".join(lines)

    return f"Câu hỏi của Admin: {question}\n\n{data_section}"


def _format_similar_repair(repair, score: float) -> str:
    creator = repair.created_by.full_name if getattr(repair, "created_by", None) else "-"
    after = repair.after_repair_result.value if repair.after_repair_result else "-"
    date_receive = repair.date_receive.isoformat() if repair.date_receive else "-"
    return (
        f"- [độ giống {score:.2f}] Repair #{repair.id} (board {repair.board_code or '-'}, "
        f"code {repair.code or '-'}, ngày nhận {date_receive}, người thực hiện {creator}): "
        f"hiện tượng=\"{repair.original_phenomenon or '-'}\", "
        f"nguyên nhân=\"{repair.failure_cause or '-'}\", cách sửa=\"{repair.disposition or '-'}\", "
        f"kết quả sau sửa={after}"
    )
