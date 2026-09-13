# app/bot/commands/repair_detail_report_command.py
import calendar
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.board import Board
from app.models.contractor import Contractor
from app.models.repairs import Repair
from app.models.user import User
from openpyxl.drawing.image import Image as ExcelImage

REPORT_TMP_DIR = "storage/tmp_reports"
CB_PREFIX = "repairdetailreport"
YEAR_PICKER_COUNT = 5

MONTH_NAMES_VI = [
    "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
    "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
]

# Header cột xlsx theo đúng thứ tự - khớp field_getter tương ứng bên dưới.
# "Tất cả cột trong bảng repairs" + vài cột resolve tên (Board Name,
# Contractor, Created By) cho dễ đọc thay vì chỉ toàn ID/code.
COLUMNS = [
    "ID", "Date Receive", "Board ID (gốc)", "Board Code", "Board Name",
    "Device Code", "Contractor", "Test Tool Result", "Failure Cause",
    "Disposition", "After Repair Result", "Charging Station Test Status",
    "Detailed Remarks", "Ticket ID", "SN", "Date Onsite",
    "Original Phenomenon", "Station Code", "Push FW",
    "Repair Photo Path", "Failure Verification Photo Path",
    "Before Test Photo 1", "Before Test Photo 2",
    "After Repair Photo 1", "After Repair Photo 2", "After Repair Photo 3",
    "Created By", "Created At", "Updated By", "Updated At",
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
    # Sinh báo cáo
    # ------------------------------------------------------------------

    def _send_report(self, chat_id, period_start, period_end, label):
        """
        Xuất TẤT CẢ cột trong bảng repairs ra file xlsx.

        Lọc theo created_at nằm trong [period_start, period_end) - vì đây là
        thời điểm record thực sự được tạo trong hệ thống (luôn có sẵn nhờ
        AuditMixin), đáng tin cậy hơn date_receive (thường bị thiếu ở data cũ).

        Sắp xếp kết quả theo date_receive GẦN NHẤT trước (giảm dần); record
        thiếu date_receive được xếp xuống cuối.
        """
        with SessionLocal() as db:
            repairs = (
                db.query(Repair)
                .filter(Repair.created_at >= period_start, Repair.created_at < period_end)
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

        rows = [self._build_row(r, board_names, contractor_names, user_names) for r in repairs]

        file_path = self._build_xlsx(rows)
        try:
            with open(file_path, "rb") as f:
                self.bot.send_document(
                    chat_id, f,
                    caption=f"📊 Báo cáo chi tiết Repair - {label} ({len(rows)} bản ghi, sắp xếp theo ngày gần nhất)"
                )
        finally:
            try:
                file_path.unlink()
            except Exception:
                pass

    @staticmethod
    def _build_row(r, board_names, contractor_names, user_names):
        def enum_val(v):
            return v.value if v else None

        return [
            r.id,
            r.date_receive,
            r.board_id,
            r.board_code,
            board_names.get(r.board_code, r.board_code),
            r.code,
            contractor_names.get(r.contractor_id, "-") if r.contractor_id else None,
            enum_val(r.test_tool_result),
            r.failure_cause,
            r.disposition,
            enum_val(r.after_repair_result),
            r.charging_station_test_status,
            r.detailed_remarks,
            r.ticket_id,
            r.sn,
            r.date_onsite,
            r.original_phenomenon,
            r.station_code,
            r.puss_f,
            r.repair_photo_path,
            r.failure_verification_photo_path,
            r.before_test_photo_1,
            r.before_test_photo_2,
            r.after_repair_photo_1,
            r.after_repair_photo_2,
            r.after_repair_photo_3,
            user_names.get(r.created_by_id, "-") if r.created_by_id else None,
            r.created_at.strftime("%d/%m/%Y %H:%M") if r.created_at else None,
            user_names.get(r.updated_by_id, "-") if r.updated_by_id else None,
            r.updated_at.strftime("%d/%m/%Y %H:%M") if r.updated_at else None,
        ]

    @staticmethod
    def _build_xlsx(rows) -> Path:
        wb = Workbook()
        ws = wb.active
        ws.title = "Repair Detail Report"

        header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4472C4")
        normal_font = Font(name="Arial", size=10)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center", wrap_text=True)

        # Nhận diện các cột chứa hình ảnh (có chữ "Photo" trong tiêu đề)
        image_col_indices = {i for i, h in enumerate(COLUMNS, start=1) if "Photo" in h}

        # Format header
        for col_idx, h in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            
            # Chỉnh độ rộng cột. Cột ảnh cho rộng hơn một chút.
            if col_idx in image_col_indices:
                ws.column_dimensions[get_column_letter(col_idx)].width = 25
            else:
                ws.column_dimensions[get_column_letter(col_idx)].width = 20

        ws.column_dimensions[get_column_letter(1)].width = 8  # ID

        # Ghi dữ liệu từng dòng
        for row_idx, row_values in enumerate(rows, start=2):
            # Tăng chiều cao của dòng để vừa với hình ảnh (ví dụ: 80 pixels)
            ws.row_dimensions[row_idx].height = 80 
            
            for col_idx, value in enumerate(row_values, start=1):
                # Xử lý nếu đây là cột ảnh và có dữ liệu đường dẫn
                if col_idx in image_col_indices and value:
                    img_path = Path(value)
                    # Kiểm tra file ảnh có thực sự tồn tại trên disk không
                    if img_path.is_file():
                        try:
                            img = ExcelImage(str(img_path))
                            # Resize ảnh cho vừa ô (100x100 px)
                            img.width = 100
                            img.height = 100
                            
                            # Tọa độ ô, ví dụ: "V2", "W2"
                            cell_address = f"{get_column_letter(col_idx)}{row_idx}"
                            ws.add_image(img, cell_address)
                            continue  # Bỏ qua việc ghi text đường dẫn
                        except Exception:
                            pass # Nếu lỗi file (không phải ảnh hợp lệ) thì fallback xuống ghi text
                            
                # Ghi text bình thường (cho các cột không phải ảnh hoặc ảnh bị lỗi/thiếu)
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = normal_font
                cell.alignment = center if col_idx in (1, 7, 8, 11) else left

        ws.freeze_panes = "A2"

        dir_path = Path(REPORT_TMP_DIR)
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"repair_detail_report_{uuid.uuid4().hex[:8]}.xlsx"
        wb.save(file_path)

        return file_path