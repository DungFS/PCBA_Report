# app/bot/commands/repair_detail_report_command.py
import calendar
import threading
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image as PILImage

from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models import TestResult
from app.models.board import Board
from app.models.contractor import Contractor
from app.models.repairs import Repair
from app.models.user import User

REPORT_TMP_DIR = "storage/tmp_reports"
CB_PREFIX = "repairdetailreport"
YEAR_PICKER_COUNT = 5
THUMBNAIL_SIZE = (120, 120)   # kích thước ảnh sau khi resize (px)
THUMBNAIL_QUALITY = 70        # chất lượng nén JPEG (0-95)

MONTH_NAMES_VI = [
    "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
    "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
]

# Cột đầu tiên "Number" là số thứ tự tự sinh (không lấy từ DB).
# Các cột còn lại PHẢI khớp đúng thứ tự giá trị trả về từ _build_row().
DETAIL_COLUMNS = [
    "Number", "Date Receive", "Board ID", "Board Code", "Board Name",
    "Device Code", "Contractor", "Before Test Photo 1", "Before Test Photo 2",
    "Test Tool Result", "Failure Cause",
    "Disposition", "After Repair Result",
    "After Repair Photo 1", "After Repair Photo 2", "After Repair Photo 3", "Charging Station Test Status",
    "Detailed Remarks", "Ticket ID", "SN", "Date Onsite",
    "Original Phenomenon", "Failure Verification Photo", "Station Code",
]

# Header được ghi text căn giữa (các cột dữ liệu ngắn, dạng phân loại).
# Các cột không có trong set này mặc định căn trái + wrap text.
CENTER_HEADERS = {"Contractor", "Test Tool Result", "After Repair Result"}

SUMMARY_COLUMNS = [
    "Material Code", "TYPE", "Quality",
    "First Test PASS rate", "After repair Test PASS rate",
    "Analytics", "Discard",
]


class RepairDetailReportCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['repair_detail_report'], admin=True)
        def handle(message):
            self._show_period_menu(message.chat.id)

        @self.bot.callback_query_handler(
            func=lambda call: bool(call.data) and call.data.startswith(f"{CB_PREFIX}:")
        )
        @self.admin_only
        def handle_callback(call):
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                pass
            self._route_callback(call)

    def _route_callback(self, call):
        chat_id = call.message.chat.id
        parts = call.data.split(":")  # ["repairdetailreport", "<action>", ...]
        action = parts[1] if len(parts) > 1 else None

        if action == "month":
            self._show_year_picker(chat_id, mode="month")
        elif action == "week":
            self._show_year_picker(chat_id, mode="week")
        elif action == "year":
            mode, year = parts[2], int(parts[3])
            if mode == "month":
                self._show_month_picker(chat_id, year)
            else:
                self._show_month_picker_for_week(chat_id, year)
        elif action == "pickmonth":
            year, month = int(parts[2]), int(parts[3])
            start, end = self._month_range(year, month)
            self._send_report(chat_id, start, end, f"Tháng {month}/{year}")
        elif action == "pickmonthforweek":
            year, month = int(parts[2]), int(parts[3])
            self._show_week_picker(chat_id, year, month)
        elif action == "week_pick":
            year, month, start_day, end_day = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
            start = datetime(year, month, start_day)
            end = datetime(year, month, end_day) + timedelta(days=1)
            label = f"Tuần {start_day:02d}-{end_day:02d}/{month:02d}/{year}"
            self._send_report(chat_id, start, end, label)

    # ------------------------------------------------------------------
    # Menu chọn Theo tháng / Theo tuần -> Năm -> (Tháng) -> (Tuần)
    # ------------------------------------------------------------------

    def _show_period_menu(self, chat_id):
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🗓 Theo tháng", callback_data=f"{CB_PREFIX}:month"),
            InlineKeyboardButton("📆 Theo tuần", callback_data=f"{CB_PREFIX}:week"),
        )
        self.bot.send_message(chat_id, "📊 Báo cáo chi tiết Repair - chọn khoảng thời gian:", reply_markup=markup)

    def _show_year_picker(self, chat_id, mode):
        current_year = datetime.now().year
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(str(y), callback_data=f"{CB_PREFIX}:year:{mode}:{y}")
            for y in range(current_year, current_year - YEAR_PICKER_COUNT, -1)
        ]
        markup.add(*buttons)
        self.bot.send_message(chat_id, "📅 Chọn năm:", reply_markup=markup)

    def _show_month_picker(self, chat_id, year):
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(MONTH_NAMES_VI[m - 1], callback_data=f"{CB_PREFIX}:pickmonth:{year}:{m}")
            for m in range(1, 13)
        ]
        markup.add(*buttons)
        self.bot.send_message(chat_id, f"🗓 Chọn tháng trong năm {year}:", reply_markup=markup)

    def _show_month_picker_for_week(self, chat_id, year):
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(MONTH_NAMES_VI[m - 1], callback_data=f"{CB_PREFIX}:pickmonthforweek:{year}:{m}")
            for m in range(1, 13)
        ]
        markup.add(*buttons)
        self.bot.send_message(chat_id, f"🗓 Chọn tháng trong năm {year}:", reply_markup=markup)

    def _show_week_picker(self, chat_id, year, month):
        markup = InlineKeyboardMarkup(row_width=1)
        for label, start_day, end_day in self._week_chunks(year, month):
            markup.add(InlineKeyboardButton(
                label, callback_data=f"{CB_PREFIX}:week_pick:{year}:{month}:{start_day}:{end_day}"
            ))
        self.bot.send_message(chat_id, f"📆 Chọn tuần trong Tháng {month}/{year}:", reply_markup=markup)

    @staticmethod
    def _month_range(year, month):
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return start, end

    @staticmethod
    def _week_chunks(year, month):
        """Chia tháng thành các tuần 7 ngày cố định (1-7, 8-14, 15-21, 22-28,
        29-cuối tháng) - luôn khớp trọn trong 1 tháng, không tràn sang tháng khác."""
        total_days = calendar.monthrange(year, month)[1]
        chunks = []
        day = 1
        idx = 1
        while day <= total_days:
            end_day = min(day + 6, total_days)
            label = f"Tuần {idx} ({day:02d}-{end_day:02d}/{month:02d})"
            chunks.append((label, day, end_day))
            day = end_day + 1
            idx += 1
        return chunks

    # ------------------------------------------------------------------
    # Sinh báo cáo - chạy nền để không chặn bot / không bị timeout
    # ------------------------------------------------------------------

    def _send_report(self, chat_id, period_start, period_end, label):
        """
        Phản hồi ngay cho người dùng, giao toàn bộ việc nặng (query DB,
        build ảnh, ghi xlsx) cho thread nền để tránh chặn handler và
        tránh timeout phía Telegram/webhook.
        """
        self.bot.send_message(chat_id, f"⏳ Đang tạo báo cáo cho \"{label}\", vui lòng chờ...")
        threading.Thread(
            target=self._build_and_send_report,
            args=(chat_id, period_start, period_end, label),
            daemon=True,
        ).start()

    def _build_and_send_report(self, chat_id, period_start, period_end, label):
        """
        Lọc theo created_at nằm trong [period_start, period_end) - vì đây là
        thời điểm record thực sự được tạo trong hệ thống (luôn có sẵn nhờ
        AuditMixin), đáng tin cậy hơn date_receive (thường bị thiếu ở data cũ).

        Sắp xếp kết quả theo date_receive GẦN NHẤT trước (giảm dần); record
        thiếu date_receive được xếp xuống cuối.

        Kết quả xuất ra 1 file xlsx nhiều sheet:
        - Sheet đầu "Tổng hợp": group theo board, đếm Quality / PASS rate / Discard.
        - Các sheet sau: 1 sheet / board, liệt kê chi tiết từng repair (kèm ảnh),
          đánh số thứ tự 1..n theo thứ tự hiển thị trong sheet đó.
        """
        try:
            with SessionLocal() as db:
                repairs = (
                    db.query(Repair)
                    .filter(Repair.date_receive >= period_start, Repair.date_receive < period_end)
                    .all()
                )
                boards = db.query(Board).all()
                contractors = db.query(Contractor).all()
                users = db.query(User).all()

            if not repairs:
                self.bot.send_message(chat_id, f"📊 Không có repair nào trong khoảng \"{label}\".")
                return

            repairs.sort(key=lambda r: r.date_receive or date.min, reverse=True)

            board_names = {b.code: (b.ten or b.code) for b in boards}
            contractor_names = {c.id: c.name for c in contractors}
            user_names = {u.id: (getattr(u, "full_name", None) or f"User #{u.id}") for u in users}

            # Group theo Board Code - mỗi board 1 sheet chi tiết
            groups: dict[str, list[Repair]] = {}
            for r in repairs:
                key = r.board_code or "__unknown__"
                groups.setdefault(key, []).append(r)

            file_path = self._build_workbook(groups, board_names, contractor_names, user_names)
            try:
                with open(file_path, "rb") as f:
                    self.bot.send_document(
                        chat_id, f,
                        caption=(
                            f"📊 Báo cáo chi tiết Repair - {label} "
                            f"({len(repairs)} bản ghi, {len(groups)} board, sắp xếp theo ngày gần nhất)"
                        )
                    )
            finally:
                try:
                    file_path.unlink()
                except Exception:
                    pass
        except Exception as e:
            try:
                self.bot.send_message(chat_id, f"❌ Có lỗi khi tạo báo cáo \"{label}\": {e}")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Phân loại PASS / FAIL / Discard cho sheet Tổng hợp - so theo enum
    # TestResult (PASS / FAIL / DISCARD), không đoán chuỗi.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pass(v) -> bool:
        return v == TestResult.PASS

    @staticmethod
    def _is_fail(v) -> bool:
        return v == TestResult.FAIL

    @staticmethod
    def _is_discard(v) -> bool:
        return v == TestResult.DISCARD

    @classmethod
    def _compute_summary_counts(cls, group_repairs):
        """
        Quality = tổng số repair của board.
        First Test PASS rate = số lượng test_tool_result == PASS.
        After repair Test PASS rate = số lượng after_repair_result == PASS.
        Phân tích = số lượng after_repair_result == FAIL.
        Discard = số lượng test_tool_result == DISCARD.
        """
        quality = len(group_repairs)
        first_pass = sum(1 for r in group_repairs if cls._is_pass(r.test_tool_result))
        after_pass = sum(1 for r in group_repairs if cls._is_pass(r.after_repair_result))
        analytics = sum(1 for r in group_repairs if cls._is_fail(r.after_repair_result))
        discard = sum(1 for r in group_repairs if cls._is_discard(r.test_tool_result))
        return quality, first_pass, after_pass, analytics, discard

    # ------------------------------------------------------------------
    # Build dòng chi tiết - thứ tự PHẢI khớp DETAIL_COLUMNS[1:] (bỏ "Number")
    # ------------------------------------------------------------------

    @staticmethod
    def _build_row(r, board_names, contractor_names, user_names):
        def enum_val(v):
            return v.value if v else None

        return [
            r.date_receive,                                                          # Date Receive
            r.board_id,                                                              # Board ID
            r.board_code,                                                            # Board Code
            board_names.get(r.board_code, r.board_code),                             # Board Name
            r.code,                                                                  # Device Code
            contractor_names.get(r.contractor_id, "-") if r.contractor_id else None, # Contractor
            r.before_test_photo_1,                                                   # Before Test Photo 1
            r.before_test_photo_2,                                                   # Before Test Photo 2
            enum_val(r.test_tool_result),                                            # Test Tool Result
            r.failure_cause,                                                         # Failure Cause
            r.disposition,                                                           # Disposition
            enum_val(r.after_repair_result),                                         # After Repair Result
            r.after_repair_photo_1,                                                  # After Repair Photo 1
            r.after_repair_photo_2,                                                  # After Repair Photo 2
            r.after_repair_photo_3,                                                  # After Repair Photo 3
            r.charging_station_test_status,                                          # Charging Station Test Status
            r.detailed_remarks,                                                      # Detailed Remarks
            r.ticket_id,                                                             # Ticket ID
            r.sn,                                                                    # SN
            r.date_onsite,                                                           # Date Onsite
            r.original_phenomenon,                                                   # Original Phenomenon
            r.failure_verification_photo_path,                                       # Failure Verification Photo
            r.station_code,                                                          # Station Code
        ]

    # ------------------------------------------------------------------
    # Ảnh: resize + nén trước khi nhúng, tránh file xlsx quá nặng
    # ------------------------------------------------------------------

    @staticmethod
    def _make_thumbnail(img_path: Path) -> BytesIO | None:
        """Resize + nén ảnh thành JPEG nhỏ gọn, trả về BytesIO để nhúng
        thẳng vào Excel mà không cần ghi file tạm."""
        try:
            with PILImage.open(img_path) as im:
                im = im.convert("RGB")
                im.thumbnail(THUMBNAIL_SIZE)  # giữ tỉ lệ, không phóng to
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
                buf.seek(0)
                return buf
        except Exception:
            return None

    @staticmethod
    def _safe_sheet_name(name: str, used_names: set) -> str:
        """Excel giới hạn tên sheet <= 31 ký tự, không chứa []:*?/\\, và
        không được trùng nhau trong cùng workbook."""
        invalid = set('[]:*?/\\')
        cleaned = "".join(c for c in (name or "Unknown") if c not in invalid).strip() or "Unknown"
        cleaned = cleaned[:31]
        base, i = cleaned, 2
        while cleaned in used_names:
            suffix = f" ({i})"
            cleaned = base[: 31 - len(suffix)] + suffix
            i += 1
        used_names.add(cleaned)
        return cleaned

    # ------------------------------------------------------------------
    # Build workbook: sheet 1 = Tổng hợp, các sheet sau = 1 sheet / board
    # ------------------------------------------------------------------

    def _build_workbook(self, groups, board_names, contractor_names, user_names) -> Path:
        wb = Workbook()
        used_sheet_names = {"Total"}

        # Sắp xếp board theo tên hiển thị cho dễ nhìn
        ordered_codes = sorted(
            groups.keys(),
            key=lambda code: board_names.get(code, code) or ""
        )

        # ---------------- Sheet Tổng hợp ----------------
        ws_summary = wb.active
        ws_summary.title = "Total"
        self._write_summary_sheet(ws_summary, ordered_codes, groups, board_names)

        # ---------------- Các sheet chi tiết theo board ----------------
        for code in ordered_codes:
            group_repairs = groups[code]
            board_display_name = board_names.get(code, code) if code != "__unknown__" else "Không xác định"
            sheet_title = self._safe_sheet_name(board_display_name or code, used_sheet_names)
            ws = wb.create_sheet(title=sheet_title)
            detail_rows = [
                self._build_row(r, board_names, contractor_names, user_names)
                for r in group_repairs
            ]
            self._write_detail_sheet(ws, detail_rows)

        dir_path = Path(REPORT_TMP_DIR)
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"repair_detail_report_{uuid.uuid4().hex[:8]}.xlsx"
        wb.save(file_path)
        return file_path

    @classmethod
    def _write_summary_sheet(cls, ws, ordered_codes, groups, board_names):
        header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4472C4")
        normal_font = Font(name="Arial", size=10)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center", wrap_text=True)

        for col_idx, h in enumerate(SUMMARY_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            ws.column_dimensions[get_column_letter(col_idx)].width = 22 if col_idx != 2 else 32

        row_idx = 2
        for code in ordered_codes:
            group_repairs = groups[code]
            material_code = None if code == "__unknown__" else code
            board_type = board_names.get(code, code) if code != "__unknown__" else "Không xác định"
            quality, first_pass, after_pass, analytics, discard = cls._compute_summary_counts(group_repairs)

            values = [material_code, board_type, quality, first_pass, after_pass, analytics, discard]
            for col_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = normal_font
                cell.alignment = left if col_idx in (1, 2) else center
            row_idx += 1

        ws.freeze_panes = "A2"

    @staticmethod
    def _write_detail_sheet(ws, rows):
        header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4472C4")
        normal_font = Font(name="Arial", size=10)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center", wrap_text=True)

        # Xác định cột ảnh / cột căn giữa theo TÊN HEADER (không hard-code
        # index) để tránh lệch khi thay đổi thứ tự DETAIL_COLUMNS về sau.
        image_col_indices = {i for i, h in enumerate(DETAIL_COLUMNS, start=1) if "Photo" in h}
        center_col_indices = {i for i, h in enumerate(DETAIL_COLUMNS, start=1) if h in CENTER_HEADERS}

        # Format header
        for col_idx, h in enumerate(DETAIL_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            ws.column_dimensions[get_column_letter(col_idx)].width = 25 if col_idx in image_col_indices else 20

        ws.column_dimensions[get_column_letter(1)].width = 8  # Number (STT)

        # Ghi dữ liệu từng dòng - cột 1 ("Number") là số thứ tự tự sinh
        for row_idx, row_values in enumerate(rows, start=2):
            ws.row_dimensions[row_idx].height = 80

            stt = row_idx - 1
            stt_cell = ws.cell(row=row_idx, column=1, value=stt)
            stt_cell.font = normal_font
            stt_cell.alignment = center

            for offset, value in enumerate(row_values, start=1):
                col_idx = offset + 1  # +1 vì cột 1 đã dùng cho "Number"

                if col_idx in image_col_indices and value:
                    img_path = Path(value)
                    if img_path.is_file():
                        thumb = RepairDetailReportCommand._make_thumbnail(img_path)
                        if thumb is not None:
                            try:
                                img = ExcelImage(thumb)
                                img.width = 100
                                img.height = 100
                                cell_address = f"{get_column_letter(col_idx)}{row_idx}"
                                ws.add_image(img, cell_address)
                                continue  # Bỏ qua việc ghi text đường dẫn
                            except Exception:
                                pass  # fallback xuống ghi text nếu vẫn lỗi

                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = normal_font
                cell.alignment = center if col_idx in center_col_indices else left

        ws.freeze_panes = "A2"