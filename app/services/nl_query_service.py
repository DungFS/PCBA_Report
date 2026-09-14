# app/services/nl_query_service.py
"""
Service cho tính năng /ask - hỏi đáp AI về dữ liệu trong DB (chỉ Admin).

Luồng xử lý (2 lượt gọi AI qua ClaudeService, xem app/services/claude_service.py):
    1) Sinh SQL: đưa AI schema DB (tự đọc từ SQLAlchemy metadata, luôn khớp
       thực tế) + câu hỏi tiếng Việt của Admin -> AI trả về 1 câu SELECT.
    2) Trả lời: chạy câu SELECT đó (read-only) lấy dữ liệu thật, đưa lại cho
       AI cùng câu hỏi gốc -> AI tổng hợp câu trả lời tự nhiên (đếm, liệt kê,
       so sánh, tìm các lỗi có mô tả tương tự nhau, đề xuất cách sửa dựa vào
       cột `disposition`/`failure_cause` của các bản ghi tương tự đã có sẵn
       trong dữ liệu - KHÔNG bịa thông tin ngoài dữ liệu được cung cấp).

An toàn: SQL do AI sinh ra bị chặn nếu không phải câu SELECT/WITH duy nhất,
hoặc có chứa các từ khoá có thể làm thay đổi dữ liệu. Ngoài ra luôn ép thêm
LIMIT để tránh kéo về quá nhiều bản ghi.
"""
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from sqlalchemy import text

from app.core.database import Base, SessionLocal
from app.services.claude_service import get_claude_service

MAX_ROWS = 200          # số dòng tối đa lấy về từ DB
MAX_ROWS_FOR_AI = 40    # số dòng tối đa đưa vào prompt bước 2 (tránh phình prompt)

# Các từ khoá không được phép xuất hiện trong câu SQL do AI sinh ra - chỉ cho
# phép đọc dữ liệu (SELECT), không cho phép bất kỳ thao tác ghi/thay đổi nào.
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "truncate", "create",
    "replace", "grant", "revoke", "call", "exec", "execute", "merge",
    "attach", "detach", "pragma", "vacuum", "reindex", "outfile", "dumpfile",
    "infile", "lock", "unlock", "rename", "shutdown", "into outfile",
)
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(k.replace(" ", r"\s+") for k in _FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_SQL_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class NLQueryError(Exception):
    """Lỗi có thể hiển thị trực tiếp cho người dùng (không phải lỗi hệ thống)."""


@dataclass
class AskResult:
    sql: str
    columns: List[str]
    row_count: int
    rows_preview: List[Tuple[Any, ...]] = field(default_factory=list)
    answer_text: str = ""


def answer_question(question: str) -> AskResult:
    """Hàm chính - gọi từ bot command. Ném NLQueryError nếu SQL AI sinh ra
    không an toàn/không hợp lệ; ném Exception khác nếu lỗi kết nối AI/DB."""
    question = (question or "").strip()
    if not question:
        raise NLQueryError("Câu hỏi trống.")

    claude = get_claude_service()

    raw_sql = claude.ask(prompt=question, system=_build_sql_system_prompt(), max_tokens=400, temperature=0.0)
    sql = _clean_sql(raw_sql)
    _validate_select_only(sql)
    sql = _ensure_limit(sql, MAX_ROWS)

    columns, rows = _run_readonly_query(sql)

    answer_text = claude.ask(
        prompt=_build_answer_prompt(question, columns, rows),
        system=_ANSWER_SYSTEM_PROMPT,
        max_tokens=900,
        temperature=0.3,
    )

    return AskResult(
        sql=sql,
        columns=columns,
        row_count=len(rows),
        rows_preview=rows[:MAX_ROWS_FOR_AI],
        answer_text=answer_text,
    )


# ----------------------------------------------------------------------
# Bước 1: sinh SQL từ câu hỏi tự nhiên
# ----------------------------------------------------------------------

def _build_sql_system_prompt() -> str:
    return (
        "Bạn là chuyên gia sinh câu lệnh SQL cho hệ quản trị MySQL.\n"
        "Dưới đây là schema (cấu trúc) database của hệ thống quản lý sửa chữa "
        "board PCBA:\n\n"
        f"{_describe_schema()}\n"
        "YÊU CẦU BẮT BUỘC:\n"
        "- Chỉ được trả về ĐÚNG 1 câu lệnh SQL duy nhất, dạng SELECT (hoặc WITH ... SELECT).\n"
        "- KHÔNG được dùng INSERT/UPDATE/DELETE/DROP/ALTER hay bất kỳ lệnh nào làm thay đổi dữ liệu.\n"
        "- KHÔNG giải thích, KHÔNG thêm markdown/code fence, KHÔNG thêm dấu ';' ở cuối.\n"
        "- Khi câu hỏi nhắc tên người dùng (vd 'user A'), so khớp gần đúng trên users.full_name bằng LIKE.\n"
        "- Khi câu hỏi hỏi về lỗi 'giống nhau'/'tương tự', hãy dùng LIKE với từ khoá "
        "trích từ câu hỏi trên các cột failure_cause/original_phenomenon/disposition, "
        "kèm theo các cột liên quan (board_code, sn, code, disposition, failure_cause, "
        "created_by_id) để bên xử lý sau có đủ dữ liệu so sánh.\n"
        "- Nếu câu hỏi cần liệt kê chi tiết, hãy SELECT thêm các cột hữu ích cho việc trả lời "
        "(đừng chỉ SELECT *), và luôn thêm LIMIT hợp lý nếu có thể trả về nhiều dòng."
    )


def _describe_schema() -> str:
    """Tự đọc schema từ SQLAlchemy metadata (Base) - luôn khớp với model thực
    tế, không cần tay đổi khi model thay đổi."""
    lines = []
    for table in Base.metadata.sorted_tables:
        table_comment = f" -- {table.comment}" if table.comment else ""
        lines.append(f"Bảng `{table.name}`{table_comment}:")
        for col in table.columns:
            flags = []
            if col.primary_key:
                flags.append("PK")
            for fk in col.foreign_keys:
                flags.append(f"FK->{fk.target_fullname}")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            comment = f" - {col.comment}" if col.comment else ""
            lines.append(f"  - {col.name} ({col.type}){flag_str}{comment}")
        lines.append("")
    lines.append(
        "Ghi chú thêm: users.role chỉ nhận giá trị 'admin' hoặc 'user'. "
        "repairs.test_tool_result và repairs.after_repair_result chỉ nhận "
        "'PASS', 'FAIL' hoặc 'DISCARD'. Cột created_by_id/updated_by_id trong "
        "repairs/boards/contractors tham chiếu tới users.id, cho biết ai là "
        "người tạo/sửa bản ghi (dùng để trả lời các câu hỏi kiểu 'user X đã "
        "làm được những gì')."
    )
    return "\n".join(lines)


def _clean_sql(raw: str) -> str:
    sql = _SQL_FENCE_RE.sub("", raw or "").strip()
    sql = sql.rstrip(";").strip()
    return sql


def _validate_select_only(sql: str) -> None:
    if not sql:
        raise NLQueryError("AI không sinh ra được câu SQL nào.")

    statements = [s for s in sql.split(";") if s.strip()]
    if len(statements) != 1:
        raise NLQueryError("Chỉ hỗ trợ chạy 1 câu truy vấn duy nhất.")

    first_word_match = re.match(r"\s*(\w+)", sql)
    first_word = (first_word_match.group(1).lower() if first_word_match else "")
    if first_word not in ("select", "with"):
        raise NLQueryError("Chỉ cho phép câu lệnh SELECT (truy vấn đọc dữ liệu).")

    forbidden = _FORBIDDEN_RE.search(sql)
    if forbidden:
        raise NLQueryError(f"Câu SQL chứa từ khoá không được phép: '{forbidden.group(0)}'.")


def _ensure_limit(sql: str, max_rows: int) -> str:
    if re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql}\nLIMIT {max_rows}"


# ----------------------------------------------------------------------
# Chạy SQL read-only
# ----------------------------------------------------------------------

def _run_readonly_query(sql: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
    with SessionLocal() as db:
        try:
            result = db.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(row) for row in result.fetchmany(MAX_ROWS)]
        except Exception as e:
            raise NLQueryError(f"Câu SQL AI sinh ra chạy bị lỗi: {e}") from e
        finally:
            # Không commit gì cả - phòng trường hợp hi hữu 1 statement SELECT
            # kèm side-effect (vd hàm có side-effect trong MySQL) lọt qua được
            # bước validate ở trên.
            db.rollback()
    return columns, rows


# ----------------------------------------------------------------------
# Bước 2: tổng hợp câu trả lời tự nhiên từ dữ liệu thật
# ----------------------------------------------------------------------

_ANSWER_SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích dữ liệu cho hệ thống quản lý sửa chữa board PCBA. "
    "Bạn sẽ nhận được câu hỏi gốc của Admin và dữ liệu thật lấy từ database "
    "(dạng bảng). Hãy trả lời NGẮN GỌN, RÕ RÀNG bằng tiếng Việt, dựa HOÀN TOÀN "
    "vào dữ liệu được cung cấp - KHÔNG bịa thêm thông tin không có trong dữ liệu.\n"
    "- Nếu dữ liệu rỗng: nói rõ không tìm thấy dữ liệu phù hợp.\n"
    "- Nếu câu hỏi yêu cầu đếm/liệt kê: trả lời số liệu cụ thể, có thể liệt kê "
    "gọn bằng gạch đầu dòng.\n"
    "- Nếu câu hỏi yêu cầu tìm lỗi tương tự nhau: chỉ ra các bản ghi có "
    "failure_cause/original_phenomenon giống hoặc gần giống nhau trong dữ liệu.\n"
    "- Nếu câu hỏi yêu cầu đề xuất cách sửa chữa: dựa vào cột `disposition` "
    "của các bản ghi có lỗi tương tự ĐÃ CÓ trong dữ liệu (đặc biệt các bản ghi "
    "có after_repair_result = PASS) để đề xuất, nêu rõ đây là dựa theo lịch sử "
    "đã từng sửa, không phải suy đoán ngoài dữ liệu.\n"
    "- Nếu dữ liệu bị cắt bớt (ghi rõ trong phần dữ liệu), hãy nói rõ kết luận "
    "dựa trên phần dữ liệu xem được, không khẳng định tuyệt đối cho toàn bộ hệ thống."
)


def _build_answer_prompt(question: str, columns: List[str], rows: List[Tuple[Any, ...]]) -> str:
    total = len(rows)
    preview = rows[:MAX_ROWS_FOR_AI]
    table_text = _rows_to_table_text(columns, preview)
    truncated_note = (
        f"\n(Dữ liệu có tổng cộng {total} dòng, chỉ hiển thị {len(preview)} dòng đầu ở trên.)"
        if total > len(preview) else ""
    )
    return (
        f"Câu hỏi của Admin: {question}\n\n"
        f"Dữ liệu truy vấn được từ database ({total} dòng):\n{table_text}{truncated_note}"
    )


def _rows_to_table_text(columns: List[str], rows: List[Tuple[Any, ...]]) -> str:
    if not rows:
        return "(không có dòng dữ liệu nào)"
    header = " | ".join(columns)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
