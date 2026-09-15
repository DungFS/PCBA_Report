#!/usr/bin/env python3
"""
Import file Excel (.xlsx) - 2 CỘT BẮT BUỘC: board_id (tên board), img -
CỘT TÙY CHỌN: date_receive. Tạo Repair mới cho mỗi dòng, với ảnh trong cột
"img" lưu vào after_repair_photo_1.

FORMAT FILE:
    - Dòng 1: header, BẮT BUỘC đủ 2 cột "board_id", "img"
      (không phân biệt hoa/thường, khoảng trắng thừa). Cột "date_receive"
      TÙY CHỌN - không có cột này, hoặc ô trống, hoặc parse lỗi -> tự dùng
      ngày chạy script.
    - Dòng 2 trở đi: data.
        + Cột "board_id": TÊN board (khớp boards.ten) - dùng để tra board.
          NẾU TÊN NÀY CHƯA TỒN TẠI trong danh mục -> TỰ ĐỘNG TẠO BOARD MỚI
          với tên đó và tự sinh mã code (code) cho Board.
        + Cột "img": 1 ảnh nhúng trong cell (không phải text) - lưu vào
          repairs.after_repair_photo_1.
        + Cột "date_receive" (tùy chọn): ngày nhận - nếu ô trống hoặc parse
          lỗi, tự fallback về ngày chạy script + ghi cảnh báo (không chặn
          cả dòng).

MẶC ĐỊNH (không đọc từ file, luôn set cố định cho MỌI dòng import):
    - test_tool_result   = FAIL   (coi như lần test đầu tiên bị lỗi)
    - after_repair_result = PASS  (coi như sau khi sửa, test lại thì PASS)
    - code                = tự sinh mới (Repair.generate_code) - mỗi dòng
      coi là 1 thiết bị vật lý riêng biệt.
    - created_by_id/updated_by_id = IMPORT_USER_ID (xem hằng số bên dưới,
      giống quy ước dùng trong import_repair.py) - áp dụng cho CẢ Board mới
      tạo lẫn Repair.

Script này CHỈ INSERT (không xóa gì cả) - không cần xác nhận trước khi chạy,
không có mode 'reset'. Board tự tạo được COMMIT NGAY (không rollback theo
Repair) - nếu dòng Repair sau đó lỗi, Board vẫn giữ lại (đúng, vì Board
không có gì sai, chỉ là dòng Repair có vấn đề khác).

Usage:
    python3 scripts/import_after_repair_photos.py /path/to/file.xlsx
"""
import sys
import uuid
from datetime import datetime
from pathlib import Path

import openpyxl

from app.core.database import SessionLocal
from app.models import TestResult
from app.models.board import Board
# Contractor và User không dùng trực tiếp, nhưng BẮT BUỘC phải import vì
# Repair model có relationship("Contractor", ...) và kế thừa AuditMixin
# (relationship("User", ...)) - SQLAlchemy cần các class này đã được import
# (đăng ký vào mapper registry) ở đâu đó trong cùng process trước khi
# mapper được configure, nếu không sẽ lỗi ngay lúc gọi db.query(Repair) đầu
# tiên.
from app.models.contractor import Contractor  # noqa: F401
from app.models.repairs import Repair
from app.models.user import User  # noqa: F401

REPAIR_PHOTO_STORAGE_DIR = "storage/repair_photos"
IMPORT_USER_ID = 1  # user gán làm "người tạo" cho mọi record import qua script này

HEADER_ROW = 1
DATA_START_ROW = 2
REQUIRED_COLUMNS = ["board_id", "img"]

_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    # Cell lưu dạng TEXT (không phải Date thật) nên có kèm giờ, thường luôn
    # là 00:00:00 - rất phổ biến khi date_receive bị convert qua
    # text lúc export file.
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)


class AfterRepairPhotoImporter:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.created = 0
        self.failed = []
        self.warnings = []

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self):
        wb = openpyxl.load_workbook(self.file_path)
        ws = wb.active

        headers_raw = [str(c.value).strip() if c.value else "" for c in ws[HEADER_ROW]]
        headers_lower = [h.lower() for h in headers_raw]

        missing = [c for c in REQUIRED_COLUMNS if c not in headers_lower]
        if missing:
            print(f"❌ File thiếu cột bắt buộc: {', '.join(missing)}")
            print(f"   Header đọc được: {headers_raw}")
            sys.exit(1)

        col_board_id = headers_lower.index("board_id") + 1  # openpyxl cột 1-indexed
        col_img = headers_lower.index("img") + 1
        col_date_receive = headers_lower.index("date_receive") + 1 if "date_receive" in headers_lower else None

        image_map, image_warnings = self._build_image_map(ws, target_col=col_img)
        self.warnings.extend(image_warnings)

        if not image_map:
            self.warnings.append(
                "Không đọc được ảnh nhúng nào trong cột 'img' (ws._images rỗng "
                "hoặc không có ảnh nào neo đúng cột). Kiểm tra: đã cài Pillow "
                "chưa (`pip install Pillow`), và ảnh có thực sự nằm trong cột "
                "'img' hay không."
            )

        board_by_name = self._load_boards_by_name()

        total_rows = ws.max_row - (DATA_START_ROW - 1)
        print(f"Bắt đầu import {max(total_rows, 0)} dòng từ '{self.file_path}' ...\n")

        with SessionLocal() as db:
            for row_num in range(DATA_START_ROW, ws.max_row + 1):
                board_name_cell = ws.cell(row=row_num, column=col_board_id).value
                board_name = str(board_name_cell).strip() if board_name_cell else ""

                if not board_name:
                    continue  # dòng trống cột board_id -> bỏ qua im lặng (không phải lỗi)

                location = f"Dòng {row_num}"
                try:
                    board, board_err = self._resolve_board(db, board_name, board_by_name)
                    if board_err:
                        raise ValueError(board_err)

                    entry = image_map.get(row_num)
                    if entry is None:
                        raise ValueError("Không tìm thấy ảnh nào ở cột 'img' cho dòng này.")

                    data, img_format = entry
                    photo_path = self._save_image(data, img_format, board.code)

                    date_receive, date_warning = self._resolve_date_receive(ws, row_num, col_date_receive)
                    if date_warning:
                        self.warnings.append(f"{location}: {date_warning}")

                    repairer_name = ""  # script chạy độc lập, không có user thật thao tác
                    device_code = Repair.generate_code(db, board.code)

                    repair = Repair(
                        date_receive=date_receive,
                        board_id=board.ten or board.code,
                        board_code=board.code,
                        code=device_code,
                        test_tool_result=TestResult.FAIL,
                        after_repair_result=TestResult.PASS,
                        after_repair_photo_1=photo_path,
                        created_by_id=IMPORT_USER_ID,
                        updated_by_id=IMPORT_USER_ID,
                    )
                    db.add(repair)
                    # Commit từng dòng - 1 dòng lỗi chỉ ảnh hưởng đúng dòng
                    # đó, không làm mất các dòng khác đã xử lý đúng.
                    db.commit()
                    self.created += 1
                except Exception as e:
                    db.rollback()
                    self.failed.append(f"{location}: {e}")
                    continue

        self._print_report()

    def _resolve_board(self, db, board_name, board_by_name):
        """Tra theo tên board (board_id).

        Nếu board với tên này CHƯA TỒN TẠI trong danh mục -> TỰ ĐỘNG TẠO Board
        mới với ten=board_name và tự sinh mã code (code) định danh, commit
        ngay lập tức (độc lập với Repair của dòng này - nếu Repair sau đó
        lỗi, Board mới tạo vẫn được giữ lại).

        Trả về (board, error_message) - đúng 1 trong 2 sẽ None."""
        name_key = board_name.lower()
        board = board_by_name.get(name_key)
        
        if board == "AMBIGUOUS":
            return None, (
                f"Có NHIỀU HƠN 1 board cùng tên '{board_name}' trong danh mục - không xác "
                f"định được nên dùng board nào. Hãy đặt tên duy nhất cho từng board trong CSDL."
            )
            
        if board is not None:
            return board, None

        # Chưa có board với tên này -> tự động tạo mới kèm mã code tự sinh.
        safe_prefix = "".join(c if c.isalnum() else "_" for c in board_name[:10]).upper()
        generated_code = f"BRD_{safe_prefix}_{uuid.uuid4().hex[:6].upper()}"
        
        board = Board(
            code=generated_code,
            ten=board_name,
            created_by_id=IMPORT_USER_ID,
            updated_by_id=IMPORT_USER_ID,
        )
        db.add(board)
        db.commit()

        board_by_name[name_key] = board

        self.warnings.append(
            f"Board mới được tạo tự động: tên='{board_name}' (mã code tự sinh: '{generated_code}')"
        )
        return board, None

    @staticmethod
    def _resolve_date_receive(ws, row_num, col_date_receive):
        """Đọc + parse date_receive từ file. Trả về (date, warning_message).
        Nếu cột không tồn tại trong file, hoặc ô trống, hoặc parse lỗi ->
        fallback về ngày hôm nay + trả warning (không raise lỗi, không chặn
        cả dòng chỉ vì date sai)."""
        if col_date_receive is None:
            return datetime.now().date(), None

        raw = ws.cell(row=row_num, column=col_date_receive).value
        if raw is None or raw == "":
            return datetime.now().date(), None

        if isinstance(raw, datetime):
            return raw.date(), None
        if hasattr(raw, "year") and hasattr(raw, "month") and hasattr(raw, "day"):
            return raw, None

        text = str(raw).strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date(), None
            except ValueError:
                continue

        return (
            datetime.now().date(),
            f"date_receive - giá trị '{text}' không khớp format ngày nào được hỗ trợ -> dùng ngày hôm nay thay thế.",
        )

    # ------------------------------------------------------------------
    # Board: load danh mục - index theo TÊN (ten, có thể trùng)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_boards_by_name() -> dict:
        """Trả về dict {ten.lower(): Board}. Nếu 2+ board trùng tên (không
        phân biệt hoa/thường) -> giá trị là chuỗi "AMBIGUOUS" thay vì Board,
        để phát hiện và báo lỗi rõ ràng thay vì dùng nhầm board."""
        with SessionLocal() as db:
            boards = db.query(Board).filter(Board.ten.isnot(None)).all()

        result = {}
        for b in boards:
            key = b.ten.strip().lower()
            if key in result:
                result[key] = "AMBIGUOUS"
            else:
                result[key] = b
        return result

    # ------------------------------------------------------------------
    # Ảnh nhúng - CHỈ lấy ảnh ở đúng CỘT "img" (target_col), map theo dòng
    # ------------------------------------------------------------------

    @classmethod
    def _build_image_map(cls, ws, target_col: int):
        """Build map row_num -> (bytes ảnh, format), CHỈ với ảnh neo đúng cột
        target_col. Trả về (image_map, warnings)."""
        image_map = {}
        warnings = []

        for i, img in enumerate(getattr(ws, "_images", [])):
            anchor = getattr(img, "anchor", None)
            if anchor is None:
                warnings.append(f"Ảnh #{i} không xác định được vị trí neo -> bị bỏ qua.")
                continue

            if hasattr(anchor, "_from"):
                row = anchor._from.row + 1
                col = anchor._from.col + 1
            else:
                pos = getattr(anchor, "pos", None)
                if pos is None:
                    warnings.append(
                        f"Ảnh #{i} có anchor kiểu {type(anchor).__name__} không hỗ trợ "
                        f"xác định vị trí -> bị bỏ qua."
                    )
                    continue
                row = cls._estimate_row_from_emu(ws, pos.y)
                col = cls._estimate_col_from_emu(ws, pos.x)
                warnings.append(
                    f"Ảnh #{i} không gắn theo ô (anchor tuyệt đối) -> vị trí "
                    f"(dòng {row}, cột {col}) chỉ là ước lượng, có thể sai."
                )

            if col != target_col:
                continue  # không phải cột "img" - bỏ qua (vd ảnh logo/trang trí khác)

            try:
                data = img._data()
            except Exception as e:
                warnings.append(f"Ảnh #{i} (dòng ~{row}) lỗi khi đọc dữ liệu: {e} -> bị bỏ qua.")
                continue

            if row in image_map:
                warnings.append(f"Có nhiều hơn 1 ảnh ở dòng {row}, cột 'img' -> chỉ giữ ảnh đầu tiên.")
            else:
                image_map[row] = (data, getattr(img, "format", None))

        return image_map, warnings

    _EMU_PER_POINT = 12700
    _DEFAULT_ROW_HEIGHT_POINTS = 15
    _DEFAULT_COL_WIDTH_EMU = 640080

    @classmethod
    def _estimate_row_from_emu(cls, ws, y_emu):
        cumulative = 0
        row = 1
        max_row = ws.max_row or 1
        while row <= max_row:
            height_pt = ws.row_dimensions[row].height or cls._DEFAULT_ROW_HEIGHT_POINTS
            row_height_emu = height_pt * cls._EMU_PER_POINT
            if cumulative + row_height_emu > y_emu:
                return row
            cumulative += row_height_emu
            row += 1
        return max_row

    @classmethod
    def _estimate_col_from_emu(cls, ws, x_emu):
        from openpyxl.utils import get_column_letter

        cumulative = 0
        col = 1
        max_col = ws.max_column or 1
        while col <= max_col:
            letter = get_column_letter(col)
            width_units = ws.column_dimensions[letter].width
            col_width_emu = (width_units * 7 + 5) * 9525 if width_units else cls._DEFAULT_COL_WIDTH_EMU
            if cumulative + col_width_emu > x_emu:
                return col
            cumulative += col_width_emu
            col += 1
        return max_col

    @staticmethod
    def _save_image(data: bytes, img_format: str, board_code: str) -> str:
        ext = (img_format or "png").lower()
        if ext == "jpeg":
            ext = "jpg"

        safe_code = "".join(c if c.isalnum() or c in "-_" else "_" for c in (board_code or "unknown"))
        subdir = datetime.now().strftime("%Y%m")
        dir_path = Path(REPAIR_PHOTO_STORAGE_DIR) / subdir
        dir_path.mkdir(parents=True, exist_ok=True)

        filename = f"{safe_code}_{uuid.uuid4().hex[:8]}.{ext}"
        file_path = dir_path / filename
        with open(file_path, "wb") as f:
            f.write(data)

        return str(file_path)

    # ------------------------------------------------------------------
    # Báo cáo kết quả
    # ------------------------------------------------------------------

    def _print_report(self):
        print()
        print(f"✅ Import xong: {self.created} bản ghi Repair mới.")

        if self.failed:
            print(f"\n❌ {len(self.failed)} dòng lỗi (bị bỏ qua hoàn toàn):")
            for line in self.failed:
                print(f"  {line}")

        if self.warnings:
            print(f"\n⚠️ {len(self.warnings)} cảnh báo:")
            for line in self.warnings:
                print(f"  {line}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/import_after_repair_photos.py /path/to/file.xlsx")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"❌ Không tìm thấy file: {file_path}")
        sys.exit(1)
    if file_path.suffix.lower() not in (".xlsx", ".xlsm"):
        print("❌ Chỉ hỗ trợ file .xlsx / .xlsm")
        sys.exit(1)

    importer = AfterRepairPhotoImporter(str(file_path))
    importer.run()


if __name__ == "__main__":
    main()