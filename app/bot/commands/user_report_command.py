# app/bot/commands/user_report_command.py
import calendar
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models import TestResult
from app.models.board import Board
from app.models.repairs import Repair
from app.models.user import User

REPORT_TMP_DIR = "storage/tmp_reports"
CB_PREFIX = "userreport"
YEAR_PICKER_COUNT = 5

MONTH_NAMES_VI = [
    "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
    "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
]


class UserReportCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['user_report'], admin=True)
        def handle(message):
            self._show_year_picker(message.chat.id)

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
        parts = call.data.split(":")  # ["userreport", "<action>", ...]
        action = parts[1] if len(parts) > 1 else None

        if action == "year":
            year = int(parts[2])
            self._show_month_picker(chat_id, year)
        elif action == "month":
            year, month = int(parts[2]), int(parts[3])
            self._show_week_picker(chat_id, year, month)
        elif action == "week":
            year, month, start_day, end_day = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
            start = datetime(year, month, start_day)
            end = datetime(year, month, end_day) + timedelta(days=1)
            label = f"Tuần {start_day:02d}-{end_day:02d}/{month:02d}/{year}"
            self._send_user_report(chat_id, start, end, label)

    # ------------------------------------------------------------------
    # Menu chọn Năm -> Tháng -> Tuần
    # ------------------------------------------------------------------

    def _show_year_picker(self, chat_id):
        current_year = datetime.now().year
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(str(y), callback_data=f"{CB_PREFIX}:year:{y}")
            for y in range(current_year, current_year - YEAR_PICKER_COUNT, -1)
        ]
        markup.add(*buttons)
        self.bot.send_message(chat_id, "📅 Báo cáo theo User - chọn năm:", reply_markup=markup)

    def _show_month_picker(self, chat_id, year):
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(MONTH_NAMES_VI[m - 1], callback_data=f"{CB_PREFIX}:month:{year}:{m}")
            for m in range(1, 13)
        ]
        markup.add(*buttons)
        self.bot.send_message(chat_id, f"🗓 Chọn tháng trong năm {year}:", reply_markup=markup)

    def _show_week_picker(self, chat_id, year, month):
        markup = InlineKeyboardMarkup(row_width=1)
        for label, start_day, end_day in self._week_chunks(year, month):
            markup.add(InlineKeyboardButton(
                label, callback_data=f"{CB_PREFIX}:week:{year}:{month}:{start_day}:{end_day}"
            ))
        self.bot.send_message(chat_id, f"📆 Chọn tuần trong Tháng {month}/{year}:", reply_markup=markup)

    @staticmethod
    def _week_chunks(year, month):
        """Chia tháng thành các tuần 7 ngày cố định (1-7, 8-14, 15-21, 22-28,
        29-cuối tháng) - KHÔNG phải tuần lịch Thứ 2 -> Chủ nhật, để đơn giản
        và luôn khớp trọn trong 1 tháng (không tràn sang tháng khác)."""
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
    def _send_user_report(self, chat_id, period_start, period_end, label):
        with SessionLocal() as db:
            repairs = (
                db.query(Repair)
                .filter(Repair.created_at.isnot(None))
                .filter(Repair.created_at >= period_start, Repair.created_at < period_end)
                .all()
            )
            users = db.query(User).all()
            boards = db.query(Board).all()

        if not repairs:
            self.bot.send_message(chat_id, f"📊 Không có repair nào trong khoảng \"{label}\".")
            return

        stats = {}
        for r in repairs:
            key = (r.created_by_id, r.board_code)
            entry = stats.setdefault(key, {
                "codes": set(), "first_pass": 0, "after_pass": 0, "after_fail": 0, "discard": 0, "hold": 0
            })
            if r.code:
                entry["codes"].add(r.code)
            if r.test_tool_result == TestResult.PASS:
                entry["first_pass"] += 1
            if r.after_repair_result == TestResult.PASS:
                entry["after_pass"] += 1
            if r.after_repair_result == TestResult.FAIL:
                entry["after_fail"] += 1
            if r.disposition == "Discard":
                entry["discard"] += 1

        user_names = {u.id: (getattr(u, "full_name", None) or f"User #{u.id}") for u in users}
        board_names = {b.code: (b.ten or b.code) for b in boards}

        rows = []
        for (user_id, board_code), entry in stats.items():
            uid_display = user_id if user_id is not None else "-"
            name = user_names.get(user_id, "(Không rõ user)")
            board_label = board_names.get(board_code, board_code or "(Chưa rõ loại bo)")
            
            # --- TÍNH TOÁN CÔNG THỨC ĐÁNH GIÁ ---
            after_pass = entry["after_pass"]
            after_fail = entry["after_fail"]
            total_resolved = after_pass + after_fail
            
            if total_resolved > 0:
                rate = (after_pass / total_resolved) * 100
                rate_str = f"{round(rate, 1)}%"
                if rate >= 90:
                    rating = "Xuất sắc"
                elif rate >= 75:
                    rating = "Tốt"
                elif rate >= 50:
                    rating = "Khá"
                else:
                    rating = "Cần cải thiện"
            else:
                rate_str = "0%"
                rating = "N/A"
            # ------------------------------------

            rows.append((
                uid_display, name, board_label,
                len(entry["codes"]), entry["first_pass"], entry["after_pass"],
                entry["after_fail"], entry["discard"],
                rate_str, rating
            ))

        # Ưu tiên sort theo xếp loại thành công, sau đó đến số lượng Qty
        rows.sort(key=lambda row: (row[9], row[3]), reverse=True)

        file_path = self._build_xlsx(rows)
        try:
            with open(file_path, "rb") as f:
                self.bot.send_document(
                    chat_id, f,
                    caption=f"📊 Báo cáo tổng hợp theo User (Kèm Đánh giá) - {label}"
                )
        finally:
            try:
                file_path.unlink()
            except Exception:
                pass

    @staticmethod
    def _build_xlsx(rows) -> Path:
        # Thêm 2 cột mới vào header
        headers = [
            "User ID", "Name", "Board Name", "Qty",
            "First Pass", "After Repair Pass", "Fail After Repair", 
            "Discard", "Success Rate (%)", "Rating"
        ]

        wb = Workbook()
        ws = wb.active
        ws.title = "User Report"

        header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4472C4")
        normal_font = Font(name="Arial", size=11)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center")

        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        for row_idx, row_values in enumerate(rows, start=2):
            for col_idx, value in enumerate(row_values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = normal_font
                cell.alignment = left if col_idx in (2, 3) else center

        name_widths = [len(str(r[1])) for r in rows] or [10]
        board_widths = [len(str(r[2])) for r in rows] or [10]

        ws.column_dimensions[get_column_letter(1)].width = 10
        ws.column_dimensions[get_column_letter(2)].width = max(18, max(name_widths) + 4)
        ws.column_dimensions[get_column_letter(3)].width = max(18, max(board_widths) + 4)
        ws.column_dimensions[get_column_letter(4)].width = 10
        ws.column_dimensions[get_column_letter(5)].width = 12
        ws.column_dimensions[get_column_letter(6)].width = 18
        ws.column_dimensions[get_column_letter(7)].width = 16
        ws.column_dimensions[get_column_letter(8)].width = 12
        ws.column_dimensions[get_column_letter(9)].width = 12
        
        # Format độ rộng cho 2 cột mới
        ws.column_dimensions[get_column_letter(10)].width = 18
        ws.column_dimensions[get_column_letter(11)].width = 15
        
        ws.freeze_panes = "A2"

        dir_path = Path(REPORT_TMP_DIR)
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"user_report_{uuid.uuid4().hex[:8]}.xlsx"
        wb.save(file_path)

        return file_path