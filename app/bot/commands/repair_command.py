# app/bot/commands/repair_command.py
import uuid
from datetime import datetime
from pathlib import Path

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import joinedload

from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models import TestResult
from app.models.board import Board
from app.models.contractor import Contractor
from app.models.repairs import Repair
from app.services.ticket_api import get_ticket_info

CB_PREFIX = "repaircrud"
REPAIR_PHOTO_STORAGE_DIR = "storage/repair_photos"


class RepairCommand(BaseCommand):
    # Session tạm trong bộ nhớ cho flow "add" (wizard nhiều bước) - key = chat_id
    _add_sessions = {}
    # Session tạm cho flow "detail" (chọn Board -> thiết bị -> record) - key = chat_id
    _detail_sessions = {}
    # Session tạm cho flow "update" (chọn Board -> thiết bị -> record -> field) - key = chat_id
    _update_sessions = {}

    def register(self):
        # Không truyền admin=True -> BaseCommand.register_handler mặc định dùng
        # self.auth (auth_required): bất kỳ user đã được cấp quyền (có trong
        # DB) đều gọi được /repair và dùng "add". Riêng "search" được chặn admin
        # thêm 1 lớp thủ công bên trong (_require_admin), vì nó cần quyền cao
        # hơn access thông thường.
        @self.register_handler(commands=['repair'])
        def handle(message):
            args = self._parse_args(message.text)
            if not args:
                self._show_menu(message.chat.id)
                return
            sub = args[0].lower()
            if sub in ("search", "detail", "update") and not self._require_admin(message):
                return
            self._dispatch(sub, message.chat.id, args[1:])

        @self.bot.callback_query_handler(
            func=lambda call: bool(call.data) and call.data.startswith(f"{CB_PREFIX}:")
        )
        @self.auth
        def handle_callback(call):
            action = call.data.split(":")[1] if ":" in call.data else None
            admin_gated_actions = (
                "search", "detail", "detailboard", "detaildevice", "detailrecord",
                "update", "updateboard", "updaterecord",
                "updatefield", "updatetest", "updateafter", "updatestation",
                "updatecontractor", "updatedone",
            )
            if action in admin_gated_actions and not self._require_admin(call):
                return
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                pass
            self._route_callback(call)

    def _require_admin(self, obj) -> bool:
        """Check quyền admin thủ công cho 1 Message hoặc CallbackQuery cụ thể,
        tái dùng đúng logic + thông báo từ chối có sẵn trong self.admin_only
        (middlewares.admin_required). Trả về True nếu là admin (an toàn để đi
        tiếp), False nếu bị từ chối (admin_required đã tự gửi thông báo)."""
        result_holder = {"ok": False}

        @self.admin_only
        def _mark_ok(o):
            result_holder["ok"] = True

        _mark_ok(obj)
        return result_holder["ok"]

    def _guarded(self, fn):
        """Bọc self.auth quanh 1 hàm sẽ được gọi lại bởi register_next_step_handler
        trong flow ADD - vì đó là 1 lần gọi handler HOÀN TOÀN MỚI, không đi qua
        register_handler ban đầu, nên current_user_id (dùng cho AuditMixin) sẽ
        luôn là None nếu không set lại ở đây. Add chỉ cần auth thường (không
        cần admin)."""
        return self.auth(fn)

    def _guarded_admin(self, fn):
        """Giống _guarded nhưng dùng self.admin_only - áp dụng cho next-step
        handler thuộc flow SEARCH (yêu cầu quyền admin)."""
        return self.admin_only(fn)

    def _dispatch(self, sub, chat_id, extra_args=()):
        if sub == "add":
            self._start_add(chat_id)
        elif sub == "search":
            if extra_args:
                # /repair search <code> - gõ thẳng code, không cần hỏi lại
                self._show_search_results(chat_id, extra_args[0])
            else:
                self._start_search(chat_id)
        elif sub == "detail":
            self._start_detail_search(chat_id)
        elif sub == "update":
            self._start_update_search(chat_id)
        else:
            self._show_help(chat_id, unknown=sub)

    # ------------------------------------------------------------------
    # Menu / help
    # ------------------------------------------------------------------

    def _show_menu(self, chat_id):
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("➕ Thêm mới", callback_data=f"{CB_PREFIX}:add"))
        markup.add(InlineKeyboardButton("🔍 Tra cứu theo Code (Admin)", callback_data=f"{CB_PREFIX}:search"))
        markup.add(InlineKeyboardButton("🔎 Tra cứu chi tiết (Admin)", callback_data=f"{CB_PREFIX}:detail"))
        markup.add(InlineKeyboardButton("✏️ Cập nhật (Admin)", callback_data=f"{CB_PREFIX}:update"))
        self.bot.send_message(chat_id, "🔧 Quản lý Repair - chọn 1 thao tác:", reply_markup=markup)

    def _show_help(self, chat_id, unknown=None):
        lines = []
        if unknown:
            lines.append(f"❌ Không hiểu lệnh con '{unknown}'.\n")
        lines.append("📋 Các lệnh con của /repair:")
        lines.append("  /repair add           - thêm repair mới (mọi user đã xác thực)")
        lines.append("  /repair search <code> - tra cứu lịch sử sửa chữa theo mã thiết bị (chỉ Admin)")
        lines.append("  /repair detail        - tra cứu chi tiết 1 record theo luồng chọn Board -> thiết bị -> record (chỉ Admin)")
        lines.append("  /repair update         - cập nhật 1 record đã có, theo cùng luồng chọn Board -> thiết bị -> record (chỉ Admin)")
        self.bot.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Format dùng chung cho SEARCH
    # ------------------------------------------------------------------

    @staticmethod
    def _format_repair_detail_line(r):
        """1 dòng tóm tắt: kết quả test, ticket, disposition (nếu có), người
        tạo + thời điểm tạo. Dùng created_at (luôn có sẵn nhờ AuditMixin) thay
        vì date_receive - vì date_receive thường bị bỏ trống trong wizard add,
        khiến hiển thị ra toàn dấu "-" trông rất xấu."""
        status = r.test_tool_result.value if r.test_tool_result else "-"
        line = f"   Ticket: {r.ticket_id or '-'} - {status}"
        if r.disposition:
            line += f" - {r.disposition}"

        creator = r.created_by.full_name if getattr(r, "created_by", None) else "Không rõ"
        created_str = r.created_at.strftime("%d/%m/%Y %H:%M") if getattr(r, "created_at", None) else "-"
        line += f"\n   👤 {creator} · 🕐 {created_str}"
        return line

    # ==================================================================
    # SEARCH - tra cứu lịch sử sửa chữa theo mã thiết bị (code) - CHỈ ADMIN
    # ==================================================================

    def _start_search(self, chat_id):
        msg = self.bot.send_message(
            chat_id,
            "🔍 Nhập mã thiết bị (code) cần tra cứu (vd: OCPP_Three_V2.6_22KWA).\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_search_code))

    def _process_search_code(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy tra cứu.")
            return

        code = (message.text or "").strip()
        if not code:
            self.bot.reply_to(message, "❌ Code không được để trống.")
            return

        self._show_search_results(message.chat.id, code, reply_to=message)

    def _show_search_results(self, chat_id, code, reply_to=None):
        with SessionLocal() as db:
            repairs = (
                db.query(Repair)
                .options(joinedload(Repair.created_by))
                .filter(Repair.code == code)
                .order_by(Repair.id.desc())
                .all()
            )

        send = (lambda text: self.bot.reply_to(reply_to, text)) if reply_to else (lambda text: self.bot.send_message(chat_id, text))

        if not repairs:
            send(f"🔍 Không tìm thấy repair nào với code '{code}'.")
            return

        lines = [f"🔍 Lịch sử sửa chữa của thiết bị '{code}' ({len(repairs)} lần):\n"]
        for r in repairs:
            lines.append(f"🔹 #{r.id}")
            lines.append(self._format_repair_detail_line(r))
            lines.append("")

        send("\n".join(lines).rstrip())

    # ==================================================================
    # DETAIL - tra cứu 1 record cụ thể theo luồng chọn Board -> thiết bị ->
    # record (nút bấm, không gõ tay) - CHỈ ADMIN
    # ==================================================================

    def _start_detail_search(self, chat_id):
        self._detail_sessions[chat_id] = {}

        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(chat_id, "❌ Chưa có board nào trong danh mục.")
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for b in boards:
            label = b.code + (f" - {b.ten}" if b.ten else "")
            markup.add(InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:detailboard:{b.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, "🔎 Tra cứu chi tiết\n\nBước 1: Chọn Board:", reply_markup=markup)

    def _handle_detail_board_selected(self, chat_id, board_id):
        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.send_message(chat_id, "❌ Board không tồn tại (có thể đã bị xóa).")
                self._detail_sessions.pop(chat_id, None)
                return

            board_code = board.code
            codes = [
                row[0] for row in
                db.query(Repair.code)
                .filter(Repair.board_code == board_code, Repair.code.isnot(None))
                .distinct()
                .all()
            ]

        if not codes:
            self.bot.send_message(chat_id, f"❌ Chưa có thiết bị nào (chưa có repair) của board '{board_code}'.")
            self._detail_sessions.pop(chat_id, None)
            return

        self._detail_sessions[chat_id] = {"codes": codes}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, code in enumerate(codes):
            markup.add(InlineKeyboardButton(code, callback_data=f"{CB_PREFIX}:detaildevice:{i}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, "Bước 2: Chọn thiết bị:", reply_markup=markup)

    def _handle_detail_device_selected(self, chat_id, idx_str):
        session = self._detail_sessions.get(chat_id)
        if not session or "codes" not in session:
            self.bot.send_message(
                chat_id,
                "⚠️ Phiên tra cứu đã hết hạn hoặc không còn tồn tại. Gõ /repair detail để bắt đầu lại."
            )
            return

        idx = int(idx_str)
        codes = session["codes"]
        if idx < 0 or idx >= len(codes):
            self.bot.send_message(chat_id, "❌ Lựa chọn không hợp lệ. Gõ /repair detail để thử lại.")
            self._detail_sessions.pop(chat_id, None)
            return

        code = codes[idx]

        with SessionLocal() as db:
            repairs = (
                db.query(Repair)
                .filter(Repair.code == code)
                .order_by(Repair.id.desc())
                .all()
            )

        if not repairs:
            self.bot.send_message(chat_id, f"❌ Không còn record nào cho thiết bị '{code}'.")
            self._detail_sessions.pop(chat_id, None)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for r in repairs:
            status = r.test_tool_result.value if r.test_tool_result else "-"
            label = f"#{r.id} - {status} - Ticket: {r.ticket_id or '-'}"
            markup.add(InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:detailrecord:{r.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, f"Bước 3: Chọn record của thiết bị '{code}':", reply_markup=markup)

    def _handle_detail_record_selected(self, chat_id, repair_id):
        with SessionLocal() as db:
            r = (
                db.query(Repair)
                .options(joinedload(Repair.created_by))
                .filter(Repair.id == repair_id)
                .first()
            )

        self._detail_sessions.pop(chat_id, None)

        if not r:
            self.bot.send_message(chat_id, f"❌ Không tìm thấy repair #{repair_id} (có thể đã bị xóa).")
            return

        self.bot.send_message(chat_id, self._format_repair_full_detail(r))

    @staticmethod
    def _format_repair_full_detail(r):
        status = r.test_tool_result.value if r.test_tool_result else "-"
        after_status = r.after_repair_result.value if r.after_repair_result else "-"
        creator = r.created_by.full_name if getattr(r, "created_by", None) else "Không rõ"
        created_str = r.created_at.strftime("%d/%m/%Y %H:%M") if getattr(r, "created_at", None) else "-"
        date_onsite_str = r.date_onsite.isoformat() if getattr(r, "date_onsite", None) else "-"

        lines = [
            f"🔎 Chi tiết Repair #{r.id}",
            "",
            f"Board: {r.board_code or r.board_id or '-'}",
            f"Thiết bị (code): {r.code or '-'}",
            f"Test tool result: {status}",
            f"Failure cause: {r.failure_cause or '-'}",
            f"Disposition: {r.disposition or '-'}",
            f"After repair result: {after_status}",
            f"Ticket ID: {r.ticket_id or '-'}",
            f"SN: {r.sn or '-'}",
            f"Date onsite: {date_onsite_str}",
            f"Original phenomenon: {r.original_phenomenon or '-'}",
            f"Station code: {r.station_code or '-'}",
            f"Repair photo: {r.repair_photo_path or '-'}",
            f"Failure verification photo: {r.failure_verification_photo_path or '-'}",
            "",
            f"👤 Tạo bởi: {creator} · 🕐 {created_str}",
        ]
        return "\n".join(lines)

    # ==================================================================
    # UPDATE - sửa 1 record đã có, theo luồng chọn Board -> thiết bị ->
    # record (giống hệt DETAIL) -> chọn field cần sửa -> nhập giá trị mới.
    # Sửa xong quay lại menu field để sửa tiếp field khác, hoặc bấm Xong.
    # CHỈ ADMIN.
    # ==================================================================

    # (label hiển thị, tên field trong model) - đủ các field text/enum quan
    # trọng. KHÔNG cho sửa board_code/code (đổi sẽ làm sai lệch định danh
    # thiết bị) và không cho sửa ảnh qua đây (upload lại phức tạp, để làm sau
    # nếu cần).
    UPDATE_FIELDS = [
        ("Test tool result", "test_tool_result"),
        ("Failure cause", "failure_cause"),
        ("Disposition", "disposition"),
        ("After repair result", "after_repair_result"),
        ("Charging station test status", "charging_station_test_status"),
        ("Nhà thầu", "contractor_id"),
        ("Ticket ID", "ticket_id"),
        ("SN", "sn"),
        ("Date onsite", "date_onsite"),
        ("Original phenomenon", "original_phenomenon"),
        ("Station code", "station_code"),
        ("Detailed remarks", "detailed_remarks"),
        ("Push FW", "puss_f"),
    ]
    UPDATE_FIELD_LABELS = dict(UPDATE_FIELDS)

    def _start_update_search(self, chat_id):
        self._update_sessions[chat_id] = {}

        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(chat_id, "❌ Chưa có board nào trong danh mục.")
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for b in boards:
            label = b.code + (f" - {b.ten}" if b.ten else "")
            markup.add(InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:updateboard:{b.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, "✏️ Cập nhật Repair\n\nBước 1: Chọn Board:", reply_markup=markup)

    def _handle_update_board_selected(self, chat_id, board_id):
        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.send_message(chat_id, "❌ Board không tồn tại (có thể đã bị xóa).")
                self._update_sessions.pop(chat_id, None)
                return

            board_code = board.code
            codes = [
                row[0] for row in
                db.query(Repair.code)
                .filter(Repair.board_code == board_code, Repair.code.isnot(None))
                .distinct()
                .all()
            ]

        if not codes:
            self.bot.send_message(chat_id, f"❌ Chưa có thiết bị nào (chưa có repair) của board '{board_code}'.")
            self._update_sessions.pop(chat_id, None)
            return

        self._update_sessions[chat_id] = {"codes": codes}

        # Bắt buộc gõ ĐÚNG mã thiết bị (exact match) - giống flow ADD, không
        # liệt kê danh sách bằng nút (có thể rất dài).
        msg = self.bot.send_message(
            chat_id,
            f"Board này có {len(codes)} thiết bị.\n"
            f"Bước 2: Nhập ĐÚNG mã thiết bị (bắt buộc):\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_update_device_exact))

    def _process_update_device_exact(self, message):
        chat_id = message.chat.id
        session = self._update_sessions.get(chat_id)
        if not session or "codes" not in session:
            self.bot.reply_to(message, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return
        if self._is_cancel(message):
            self._update_sessions.pop(chat_id, None)
            self.bot.reply_to(message, "❎ Đã hủy cập nhật.")
            return

        query = (message.text or "").strip()
        if not query:
            msg = self.bot.reply_to(message, "❌ Bắt buộc nhập mã thiết bị. Nhập lại:")
            self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_update_device_exact))
            return

        codes = session.get("codes", [])
        exact_matches = [c for c in codes if c.lower() == query.lower()]

        if not exact_matches:
            msg = self.bot.reply_to(
                message,
                f"❌ Không tìm thấy thiết bị nào có mã chính xác là '{query}'.\n"
                f"Nhập lại ĐÚNG mã thiết bị (bắt buộc):"
            )
            self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_update_device_exact))
            return

        session.pop("codes", None)
        self._show_update_record_list(message.chat.id, exact_matches[0], reply_to=message)

    def _show_update_record_list(self, chat_id, code, reply_to=None):
        with SessionLocal() as db:
            repairs = (
                db.query(Repair)
                .filter(Repair.code == code)
                .order_by(Repair.id.desc())
                .all()
            )

        send = (lambda text, **kw: self.bot.reply_to(reply_to, text, **kw)) if reply_to else \
               (lambda text, **kw: self.bot.send_message(chat_id, text, **kw))

        if not repairs:
            send(f"❌ Không còn record nào cho thiết bị '{code}'.")
            self._update_sessions.pop(chat_id, None)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for r in repairs:
            status = r.test_tool_result.value if r.test_tool_result else "-"
            label = f"#{r.id} - {status} - Ticket: {r.ticket_id or '-'}"
            markup.add(InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:updaterecord:{r.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        send(f"Bước 3: Chọn record của thiết bị '{code}' cần cập nhật:", reply_markup=markup)

    def _handle_update_record_selected(self, chat_id, repair_id):
        with SessionLocal() as db:
            r = db.query(Repair).options(joinedload(Repair.created_by)).filter(Repair.id == repair_id).first()

        if not r:
            self.bot.send_message(chat_id, f"❌ Không tìm thấy repair #{repair_id} (có thể đã bị xóa).")
            self._update_sessions.pop(chat_id, None)
            return

        self._update_sessions[chat_id] = {"repair_id": repair_id}
        self.bot.send_message(chat_id, self._format_repair_full_detail(r))
        self._show_update_field_menu(chat_id)

    def _get_repair_for_update(self, chat_id):
        """Lấy lại Repair (kèm contractor) theo repair_id trong session update -
        dùng để hiển thị giá trị hiện tại. Trả về None nếu session/record mất,
        và tự gửi thông báo lỗi phù hợp trong trường hợp đó."""
        session = self._update_sessions.get(chat_id)
        repair_id = session.get("repair_id") if session else None
        if not repair_id:
            self.bot.send_message(chat_id, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return None

        with SessionLocal() as db:
            r = db.query(Repair).options(joinedload(Repair.contractor)).filter(Repair.id == repair_id).first()

        if not r:
            self.bot.send_message(chat_id, f"❌ Không tìm thấy repair #{repair_id} (có thể đã bị xóa).")
            self._update_sessions.pop(chat_id, None)
            return None

        return r

    @staticmethod
    def _format_field_value(r, field):
        if field in ("test_tool_result", "after_repair_result"):
            v = getattr(r, field, None)
            return v.value if v else "-"
        if field == "contractor_id":
            return r.contractor.name if getattr(r, "contractor", None) else "-"
        if field == "date_onsite":
            return r.date_onsite.isoformat() if getattr(r, "date_onsite", None) else "-"
        value = getattr(r, field, None)
        return str(value) if value not in (None, "") else "-"

    def _show_update_field_menu(self, chat_id):
        r = self._get_repair_for_update(chat_id)
        if not r:
            return

        lines = ["Chọn field cần cập nhật (giá trị hiện tại ghi bên cạnh):", ""]
        for label, field in self.UPDATE_FIELDS:
            lines.append(f"  {label}: {self._format_field_value(r, field)}")

        markup = InlineKeyboardMarkup(row_width=1)
        for label, field in self.UPDATE_FIELDS:
            markup.add(InlineKeyboardButton(f"✏️ {label}", callback_data=f"{CB_PREFIX}:updatefield:{field}"))
        markup.add(InlineKeyboardButton("✅ Xong", callback_data=f"{CB_PREFIX}:updatedone"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
        self.bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)

    def _handle_update_done(self, chat_id):
        self._update_sessions.pop(chat_id, None)
        self.bot.send_message(chat_id, "✅ Đã hoàn tất cập nhật.")

    def _handle_update_field_selected(self, chat_id, field):
        session = self._update_sessions.get(chat_id)
        if not session or "repair_id" not in session:
            self.bot.send_message(chat_id, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return

        r = self._get_repair_for_update(chat_id)
        if not r:
            return

        label = self.UPDATE_FIELD_LABELS.get(field, field)
        current_value = self._format_field_value(r, field)
        current_line = f"Giá trị hiện tại: {current_value}\n"

        if field == "test_tool_result":
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("✅ PASS", callback_data=f"{CB_PREFIX}:updatetest:PASS"),
                InlineKeyboardButton("❌ FAIL", callback_data=f"{CB_PREFIX}:updatetest:FAIL"),
                InlineKeyboardButton("🗑 DISCARD", callback_data=f"{CB_PREFIX}:updatetest:DISCARD"),
            )
            markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
            self.bot.send_message(chat_id, f"{current_line}Chọn giá trị mới cho '{label}':", reply_markup=markup)
            return

        if field == "after_repair_result":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("✅ PASS", callback_data=f"{CB_PREFIX}:updateafter:PASS"),
                InlineKeyboardButton("❌ FAIL", callback_data=f"{CB_PREFIX}:updateafter:FAIL"),
            )
            markup.add(InlineKeyboardButton("🚫 Bỏ trống", callback_data=f"{CB_PREFIX}:updateafter:CLEAR"))
            markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
            self.bot.send_message(chat_id, f"{current_line}Chọn giá trị mới cho '{label}':", reply_markup=markup)
            return

        if field == "charging_station_test_status":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("⏳ Pending", callback_data=f"{CB_PREFIX}:updatestation:pending"),
                InlineKeyboardButton("✅ OK", callback_data=f"{CB_PREFIX}:updatestation:ok"),
            )
            markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
            self.bot.send_message(chat_id, f"{current_line}Chọn giá trị mới cho '{label}':", reply_markup=markup)
            return

        if field == "contractor_id":
            with SessionLocal() as db:
                contractors = db.query(Contractor).order_by(Contractor.id).all()
            if not contractors:
                self.bot.send_message(chat_id, "❌ Chưa có nhà thầu nào trong danh mục (dùng /contractor add để thêm).")
                self._show_update_field_menu(chat_id)
                return
            markup = InlineKeyboardMarkup(row_width=1)
            for c in contractors:
                markup.add(InlineKeyboardButton(c.name, callback_data=f"{CB_PREFIX}:updatecontractor:{c.id}"))
            markup.add(InlineKeyboardButton("🚫 Bỏ trống", callback_data=f"{CB_PREFIX}:updatecontractor:clear"))
            markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
            self.bot.send_message(chat_id, f"{current_line}Chọn giá trị mới cho '{label}':", reply_markup=markup)
            return

        # Các field còn lại: nhập text tự do. Gõ '-' để xóa trắng (set NULL).
        session["_editing_field"] = field
        msg = self.bot.send_message(
            chat_id,
            f"{current_line}Nhập giá trị mới cho '{label}':\nGõ '-' để xóa trắng, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_update_text_field))

    def _process_update_text_field(self, message):
        chat_id = message.chat.id
        session = self._update_sessions.get(chat_id)
        if not session or "repair_id" not in session:
            self.bot.reply_to(message, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return
        if self._is_cancel(message):
            self._update_sessions.pop(chat_id, None)
            self.bot.reply_to(message, "❎ Đã hủy cập nhật.")
            return

        field = session.get("_editing_field")
        raw = (message.text or "").strip()
        value = None if raw == "-" else raw

        if field == "date_onsite" and value is not None:
            parsed, err = self._parse_date(value)
            if err:
                msg = self.bot.reply_to(message, f"❌ {err}\nNhập lại (vd 8/17/2026), hoặc '-' để xóa trắng:")
                self.bot.register_next_step_handler(msg, self._guarded_admin(self._process_update_text_field))
                return
            value = parsed

        self._apply_update(message, {field: value})

    def _apply_update(self, message, field_values: dict):
        chat_id = message.chat.id
        session = self._update_sessions.get(chat_id)
        repair_id = session.get("repair_id") if session else None
        if not repair_id:
            self.bot.reply_to(message, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return

        with SessionLocal() as db:
            r = db.query(Repair).filter(Repair.id == repair_id).first()
            if not r:
                self.bot.reply_to(message, f"❌ Không tìm thấy repair #{repair_id} (có thể đã bị xóa).")
                self._update_sessions.pop(chat_id, None)
                return
            try:
                for field, value in field_values.items():
                    setattr(r, field, value)
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Cập nhật thất bại: {e}")
                return

        session.pop("_editing_field", None)
        self.bot.reply_to(message, "✅ Đã cập nhật.")
        self._show_update_field_menu(chat_id)

    def _handle_update_test_selected(self, chat_id, value):
        self._apply_update_from_callback(chat_id, {"test_tool_result": self._to_enum(value)})

    def _handle_update_after_selected(self, chat_id, value):
        new_value = None if value == "CLEAR" else self._to_enum(value)
        self._apply_update_from_callback(chat_id, {"after_repair_result": new_value})

    def _handle_update_station_selected(self, chat_id, value):
        self._apply_update_from_callback(chat_id, {"charging_station_test_status": value})

    def _handle_update_contractor_selected(self, chat_id, value):
        new_value = None if value == "clear" else int(value)
        self._apply_update_from_callback(chat_id, {"contractor_id": new_value})

    def _apply_update_from_callback(self, chat_id, field_values: dict):
        """Giống _apply_update nhưng gọi từ callback (nút bấm) thay vì next-step
        message - không có 1 Message thật để reply_to, nên dùng send_message
        thẳng vào chat_id."""
        session = self._update_sessions.get(chat_id)
        repair_id = session.get("repair_id") if session else None
        if not repair_id:
            self.bot.send_message(chat_id, "⚠️ Phiên cập nhật đã hết hạn. Gõ /repair update để bắt đầu lại.")
            return

        with SessionLocal() as db:
            r = db.query(Repair).filter(Repair.id == repair_id).first()
            if not r:
                self.bot.send_message(chat_id, f"❌ Không tìm thấy repair #{repair_id} (có thể đã bị xóa).")
                self._update_sessions.pop(chat_id, None)
                return
            try:
                for field, value in field_values.items():
                    setattr(r, field, value)
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.send_message(chat_id, f"❌ Cập nhật thất bại: {e}")
                return

        self.bot.send_message(chat_id, "✅ Đã cập nhật.")
        self._show_update_field_menu(chat_id)

    # ==================================================================
    # ADD - wizard nhiều bước (gộp từ command_board_command.py cũ)
    # ==================================================================

    def _start_add(self, chat_id):
        self._add_sessions[chat_id] = {}

        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(
                chat_id,
                "❌ Chưa có board nào trong danh mục. Dùng /board add để thêm board trước."
            )
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for b in boards:
            label = b.code + (f" - {b.ten}" if b.ten else "")
            markup.add(InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:addboard:{b.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, "➕ Thêm Repair mới\n\nBước 1: Chọn Board:", reply_markup=markup)

    def _handle_add_board_selected(self, chat_id, board_id):
        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.send_message(chat_id, "❌ Board không tồn tại (có thể đã bị xóa). Gõ /repair add để thử lại.")
                self._add_sessions.pop(chat_id, None)
                return

            board_code = board.code
            self._add_sessions[chat_id]["board_code"] = board_code
            self._add_sessions[chat_id]["board_name"] = board.ten

            existing_codes = [
                row[0] for row in
                db.query(Repair.code)
                .filter(Repair.board_code == board_code, Repair.code.isnot(None))
                .distinct()
                .all()
            ]

            if not existing_codes:
                # Không có thiết bị nào của board này trong các lần sửa trước
                # -> tự tạo code mới, không cần hỏi.
                new_code = Repair.generate_code(db, board_code)
                self._add_sessions[chat_id]["code"] = new_code
                self.bot.send_message(
                    chat_id,
                    f"ℹ️ Chưa có thiết bị nào của board '{board_code}' trước đây.\n"
                    f"Đã tự tạo mã thiết bị mới: {new_code}"
                )
                self._ask_contractor(chat_id)
                return

        # Có sẵn thiết bị -> cho chọn giữa 2 thao tác: tìm thiết bị (gõ đúng
        # mã) hoặc thêm mới ngay (không cần gõ gì).
        self._add_sessions[chat_id]["_device_choices"] = existing_codes
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔍 Tìm thiết bị", callback_data=f"{CB_PREFIX}:devicesearch"),
            InlineKeyboardButton("➕ Thêm mới", callback_data=f"{CB_PREFIX}:devicenew"),
        )
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
        self.bot.send_message(
            chat_id,
            f"Board này đã có {len(existing_codes)} thiết bị. Chọn thao tác:",
            reply_markup=markup
        )

    def _handle_device_search_button(self, chat_id):
        msg = self.bot.send_message(
            chat_id,
            "🔍 Nhập ĐÚNG mã thiết bị (bắt buộc):\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_device_exact))

    def _handle_device_new_button(self, chat_id):
        session = self._add_sessions.get(chat_id)
        if not session or "board_code" not in session:
            self.bot.send_message(chat_id, "⚠️ Phiên đã hết hạn. Gõ /repair add để bắt đầu lại.")
            return

        board_code = session["board_code"]
        with SessionLocal() as db:
            code = Repair.generate_code(db, board_code)
        session["code"] = code
        session.pop("_device_choices", None)
        self.bot.send_message(chat_id, f"✅ Đã tạo mã thiết bị mới: {code}")
        self._ask_contractor(chat_id)

    def _process_add_device_exact(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        query = (message.text or "").strip()
        session = self._add_sessions[chat_id]

        if not query:
            msg = self.bot.reply_to(message, "❌ Bắt buộc nhập mã thiết bị. Nhập lại:")
            self.bot.register_next_step_handler(msg, self._guarded(self._process_add_device_exact))
            return

        choices = session.get("_device_choices", [])
        exact_matches = [c for c in choices if c.lower() == query.lower()]

        if not exact_matches:
            msg = self.bot.reply_to(
                message,
                f"❌ Không tìm thấy thiết bị nào có mã chính xác là '{query}'.\n"
                f"Nhập lại ĐÚNG mã thiết bị (bắt buộc):"
            )
            self.bot.register_next_step_handler(msg, self._guarded(self._process_add_device_exact))
            return

        session["code"] = exact_matches[0]
        session.pop("_device_choices", None)
        self.bot.reply_to(message, f"✅ Đã chọn thiết bị: {exact_matches[0]}")
        self._ask_contractor(chat_id)

    def _ask_contractor(self, chat_id):
        with SessionLocal() as db:
            contractors = db.query(Contractor).order_by(Contractor.id).all()

        if not contractors:
            # Chưa có nhà thầu nào trong danh mục -> bỏ qua bước này, không
            # chặn cả flow tạo repair. Admin có thể thêm nhà thầu qua
            # /contractor add rồi cập nhật lại sau nếu cần.
            self.bot.send_message(
                chat_id,
                "ℹ️ Chưa có nhà thầu nào trong danh mục (dùng /contractor add để thêm) - bỏ qua bước chọn nhà thầu."
            )
            self._ask_before_test_photos(chat_id)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for c in contractors:
            markup.add(InlineKeyboardButton(c.name, callback_data=f"{CB_PREFIX}:addcontractor:{c.id}"))
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))

        self.bot.send_message(chat_id, "Chọn nhà thầu thực hiện sửa chữa:", reply_markup=markup)

    def _handle_add_contractor_selected(self, chat_id, contractor_id):
        with SessionLocal() as db:
            contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
            if not contractor:
                self.bot.send_message(chat_id, "❌ Nhà thầu không tồn tại (có thể đã bị xóa). Gõ /repair add để thử lại.")
                self._add_sessions.pop(chat_id, None)
                return
            name = contractor.name

        self._add_sessions[chat_id]["contractor_id"] = contractor_id
        self.bot.send_message(chat_id, f"✅ Đã chọn nhà thầu: {name}")
        self._ask_before_test_photos(chat_id)

    # ------------------------------------------------------------------
    # Ảnh TRƯỚC KHI TEST (tối đa 2 ảnh) - áp dụng cho MỌI kết quả test
    # (PASS/FAIL/Discard), vì luôn chụp trước khi biết kết quả.
    # ------------------------------------------------------------------

    def _ask_before_test_photos(self, chat_id):
        self._ask_photo_batch(
            chat_id, "before_test_photo", 2, "TRƯỚC KHI TEST", self._ask_test_result
        )

    def _ask_test_result(self, chat_id):
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("✅ PASS", callback_data=f"{CB_PREFIX}:addtest:PASS"),
            InlineKeyboardButton("❌ FAIL", callback_data=f"{CB_PREFIX}:addtest:FAIL"),
            InlineKeyboardButton("🗑 Discard", callback_data=f"{CB_PREFIX}:addtest:DISCARD"),
        )
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
        self.bot.send_message(chat_id, "Bước 3: Kết quả test (test_tool_result):", reply_markup=markup)

    # Nếu chọn Discard ngay ở bước test_tool_result: đi thẳng sang ticket_id,
    # bỏ qua toàn bộ bước failure_cause/disposition/after_repair/ảnh.
    SKIP_REPAIR_DISPOSITIONS = ("Discard",)

    def _handle_add_test_selected(self, chat_id, value):
        self._add_sessions[chat_id]["test_tool_result"] = value

        if value == "DISCARD":
            self._add_sessions[chat_id]["disposition"] = "Discard"
            self._ask_ticket_id(chat_id)
        elif value == "PASS":
            self._ask_ticket_id(chat_id)
        else:  # FAIL
            msg = self.bot.send_message(chat_id, "❌ FAIL - Nhập failure_cause (nguyên nhân lỗi):\nGõ /cancel để hủy.")
            self.bot.register_next_step_handler(msg, self._guarded(self._process_add_failure_cause))

    def _process_add_failure_cause(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        text = (message.text or "").strip()
        if not text:
            self.bot.reply_to(message, "❌ failure_cause không được để trống.")
            return

        self._add_sessions[chat_id]["failure_cause"] = text

        # Disposition giờ chỉ nhập text tự do, không còn lựa chọn cố định nào.
        msg = self.bot.reply_to(message, "Nhập disposition (vd: repair U12, repair C46):\nGõ /cancel để hủy.")
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_disposition_text))

    def _ask_charging_station_status(self, chat_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("⏳ Pending", callback_data=f"{CB_PREFIX}:addstation:pending"),
            InlineKeyboardButton("✅ OK", callback_data=f"{CB_PREFIX}:addstation:ok"),
        )
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
        self.bot.send_message(
            chat_id,
            "Trạng thái kiểm tra vận hành tại trạm sạc (charging_station_test_status):",
            reply_markup=markup
        )

    def _ask_ticket_id(self, chat_id):
        msg = self.bot.send_message(chat_id, "Nhập ticket ID:\nGõ /cancel để hủy.")
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_ticket_id))

    def _handle_add_station_status_selected(self, chat_id, value):
        self._add_sessions[chat_id]["charging_station_test_status"] = value
        self._ask_ticket_id(chat_id)

    def _process_add_disposition_text(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        text = (message.text or "").strip()
        if not text:
            self.bot.reply_to(message, "❌ disposition không được để trống.")
            return

        self._add_sessions[chat_id]["disposition"] = text

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ PASS", callback_data=f"{CB_PREFIX}:addafter:PASS"),
            InlineKeyboardButton("❌ FAIL", callback_data=f"{CB_PREFIX}:addafter:FAIL"),
        )
        markup.add(InlineKeyboardButton("❎ Hủy", callback_data=f"{CB_PREFIX}:cancel"))
        self.bot.reply_to(message, "Kết quả sau khi sửa (after_repair_result):", reply_markup=markup)

    def _handle_add_after_repair_selected(self, chat_id, value):
        self._add_sessions[chat_id]["after_repair_result"] = value
        self._ask_photo_batch(
            chat_id, "after_repair_photo", 3, "SAU KHI SỬA XONG VÀ PASS",
            self._ask_charging_station_status
        )

    def _process_add_ticket_id(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        ticket_id = (message.text or "").strip()
        if not ticket_id:
            self.bot.reply_to(message, "❌ ticket ID không được để trống.")
            return

        self._add_sessions[chat_id]["ticket_id"] = ticket_id

        ticket_info = get_ticket_info(ticket_id)
        if ticket_info:
            session = self._add_sessions[chat_id]
            session["sn"] = ticket_info.get("sn")
            session["date_onsite"] = ticket_info.get("date_onsite")
            session["original_phenomenon"] = ticket_info.get("original_phenomenon")
            session["station_code"] = ticket_info.get("station_code")
            session["failure_verification_photo_path"] = ticket_info.get("failure_verification_photo_path")
            session["_ticket_source"] = "api"
            self._finalize_add(message)
            return

        # API không trả về ticket (không tìm thấy / chưa implement) -> nhập tay
        # đầy đủ các field lẽ ra lấy từ API.
        self._add_sessions[chat_id]["_ticket_source"] = "manual"
        msg = self.bot.reply_to(
            message,
            "⚠️ Không tìm thấy thông tin ticket qua API. Vui lòng nhập thủ công.\n\n"
            "Nhập SN (Serial Number):\nGõ '-' nếu không có, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_sn))

    def _process_add_manual_sn(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        self._add_sessions[chat_id]["sn"] = self._none_if_dash(message.text)

        msg = self.bot.reply_to(
            message,
            "Nhập Date onsite (định dạng vd 8/17/2026):\nGõ '-' nếu không có, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_date_onsite))

    def _process_add_manual_date_onsite(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        raw = (message.text or "").strip()
        if raw == "-":
            value = None
        else:
            value, err = self._parse_date(raw)
            if err:
                msg = self.bot.reply_to(
                    message,
                    f"❌ {err}\nNhập lại Date onsite (vd 8/17/2026), hoặc '-' nếu không có:"
                )
                self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_date_onsite))
                return

        self._add_sessions[chat_id]["date_onsite"] = value

        msg = self.bot.reply_to(
            message,
            "Nhập Original phenomenon (hiện tượng lỗi ban đầu):\nGõ '-' nếu không có, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_original_phenomenon))

    def _process_add_manual_original_phenomenon(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        self._add_sessions[chat_id]["original_phenomenon"] = self._none_if_dash(message.text)

        msg = self.bot.reply_to(
            message,
            "Nhập Station code:\nGõ '-' nếu không có, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_station_code))

    def _process_add_manual_station_code(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        self._add_sessions[chat_id]["station_code"] = self._none_if_dash(message.text)

        msg = self.bot.reply_to(
            message,
            "📷 Gửi ảnh Failure verification photo (ảnh xác minh hiện tượng lỗi thực tế):\n"
            "Gõ '-' nếu không có ảnh, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_failure_photo))

    def _process_add_manual_failure_photo(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        if (message.text or "").strip() == "-":
            self._add_sessions[chat_id]["failure_verification_photo_path"] = None
            self._finalize_add(message)
            return

        if not message.photo:
            msg = self.bot.reply_to(
                message,
                "❌ Chưa nhận được ảnh. Gửi ảnh, gõ '-' để bỏ qua, hoặc /cancel để hủy."
            )
            self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_failure_photo))
            return

        code = self._add_sessions[chat_id].get("code", "unknown")
        try:
            photo_path = self._save_uploaded_photo(message, code)
        except Exception as e:
            msg = self.bot.reply_to(
                message,
                f"❌ Lưu ảnh thất bại: {e}\nGửi lại ảnh, gõ '-' để bỏ qua, hoặc /cancel để hủy."
            )
            self.bot.register_next_step_handler(msg, self._guarded(self._process_add_manual_failure_photo))
            return

        self._add_sessions[chat_id]["failure_verification_photo_path"] = photo_path
        self._finalize_add(message)

    def _finalize_add(self, message):
        chat_id = message.chat.id
        data = self._add_sessions.get(chat_id, {})
        ticket_id = data.get("ticket_id")

        with SessionLocal() as db:
            try:
                repair = Repair(
                    date_receive=datetime.now().date(),
                    board_id=data.get("board_name") or data.get("board_code"),
                    board_code=data.get("board_code"),
                    contractor_id=data.get("contractor_id"),
                    code=data.get("code"),
                    test_tool_result=self._to_enum(data.get("test_tool_result")),
                    failure_cause=data.get("failure_cause"),
                    disposition=data.get("disposition"),
                    after_repair_result=self._to_enum(data.get("after_repair_result")),
                    repair_photo_path=data.get("repair_photo_path"),
                    ticket_id=ticket_id,
                    sn=data.get("sn"),
                    date_onsite=data.get("date_onsite"),
                    original_phenomenon=data.get("original_phenomenon"),
                    station_code=data.get("station_code"),
                    failure_verification_photo_path=data.get("failure_verification_photo_path"),
                    charging_station_test_status=data.get("charging_station_test_status"),
                    before_test_photo_1=data.get("before_test_photo_1"),
                    before_test_photo_2=data.get("before_test_photo_2"),
                    after_repair_photo_1=data.get("after_repair_photo_1"),
                    after_repair_photo_2=data.get("after_repair_photo_2"),
                    after_repair_photo_3=data.get("after_repair_photo_3"),
                )
                db.add(repair)
                db.commit()
                db.refresh(repair)
                repair_id = repair.id
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Tạo record thất bại: {e}")
                return

        self._add_sessions.pop(chat_id, None)

        summary_lines = [
            f"✅ Đã tạo Repair #{repair_id}",
            f"  Board: {data.get('board_code')}",
            f"  Thiết bị (code): {data.get('code')}",
            f"  Test tool result: {data.get('test_tool_result')}",
        ]
        if data.get("test_tool_result") == "DISCARD":
            summary_lines.append(f"  Disposition: {data.get('disposition')}")
        elif data.get("test_tool_result") == "FAIL":
            summary_lines.append(f"  Failure cause: {data.get('failure_cause')}")
            summary_lines.append(f"  Disposition: {data.get('disposition')}")
            if data.get("disposition") not in self.SKIP_REPAIR_DISPOSITIONS:
                summary_lines.append(f"  After repair result: {data.get('after_repair_result')}")
                after_photo_count = sum(1 for i in (1, 2, 3) if data.get(f"after_repair_photo_{i}"))
                summary_lines.append(f"  Ảnh sau khi sửa xong: {after_photo_count} ảnh")
        summary_lines.append(f"  Ticket ID: {ticket_id}")

        if data.get("_ticket_source") == "manual":
            summary_lines.append("  ℹ️ Không tìm thấy qua API - các field SN/Date onsite/Original phenomenon/Station code/Failure verification photo đã được nhập thủ công.")
        elif data.get("_ticket_source") != "api":
            summary_lines.append("  ⚠️ Chưa xác định được nguồn dữ liệu ticket bổ sung.")

        self.bot.reply_to(message, "\n".join(summary_lines))

    def _check_add_session(self, message) -> bool:
        chat_id = message.chat.id
        if chat_id not in self._add_sessions:
            self.bot.reply_to(message, "⚠️ Phiên đã hết hạn hoặc chưa bắt đầu. Gõ /repair add để bắt đầu lại.")
            return False
        return True

    def _cancel_add(self, chat_id):
        self._add_sessions.pop(chat_id, None)
        self.bot.send_message(chat_id, "❎ Đã hủy thêm repair.")

    @staticmethod
    def _none_if_dash(text):
        t = (text or "").strip()
        return None if (not t or t == "-") else t

    def _save_uploaded_photo(self, message, code: str) -> str:
        file_id = message.photo[-1].file_id
        file_info = self.bot.get_file(file_id)
        data = self.bot.download_file(file_info.file_path)

        ext = Path(file_info.file_path).suffix or ".jpg"
        safe_code = "".join(c if c.isalnum() or c in "-_" else "_" for c in (code or "unknown"))
        subdir = datetime.now().strftime("%Y%m")
        dir_path = Path(REPAIR_PHOTO_STORAGE_DIR) / subdir
        dir_path.mkdir(parents=True, exist_ok=True)

        filename = f"{safe_code}_{uuid.uuid4().hex[:8]}{ext}"
        file_path = dir_path / filename
        with open(file_path, "wb") as f:
            f.write(data)

        return str(file_path)

    # ------------------------------------------------------------------
    # Hỏi 1 batch ảnh (tối đa N ảnh) - dùng chung cho before_test_photo_1/2
    # và after_repair_photo_1/2. Field lưu vào session dạng "{prefix}_{i}"
    # (vd "before_test_photo_1"), khớp trực tiếp tên cột trong model Repair.
    # ------------------------------------------------------------------

    def _ask_photo_batch(self, chat_id, field_prefix, max_count, label, on_done):
        session = self._add_sessions[chat_id]
        session["_photo_batch"] = {"prefix": field_prefix, "count": 0, "max": max_count, "label": label}
        session["_photo_batch_on_done"] = on_done
        msg = self.bot.send_message(
            chat_id,
            f"📷 Gửi ảnh {label} (tối đa {max_count} ảnh).\n"
            f"Gửi ảnh, hoặc gõ 'xong' để bỏ qua/kết thúc.\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_photo_batch))

    def _process_photo_batch(self, message):
        chat_id = message.chat.id
        if not self._check_add_session(message):
            return
        if self._is_cancel(message):
            self._cancel_add(chat_id)
            return

        session = self._add_sessions[chat_id]
        batch = session.get("_photo_batch")
        on_done = session.get("_photo_batch_on_done")
        if not batch or not on_done:
            # Không còn thông tin batch (bug/session lỗi) -> bỏ qua an toàn,
            # không chặn cả flow tạo repair.
            self.bot.reply_to(message, "⚠️ Có lỗi với phiên upload ảnh, bỏ qua bước này.")
            self._ask_test_result(chat_id)
            return

        text = (message.text or "").strip().lower()
        if text in ("xong", "done", "-", "skip"):
            session.pop("_photo_batch", None)
            session.pop("_photo_batch_on_done", None)
            on_done(chat_id)
            return

        if not message.photo:
            msg = self.bot.reply_to(
                message,
                "❌ Vui lòng gửi ảnh, hoặc gõ 'xong' để bỏ qua/kết thúc."
            )
            self.bot.register_next_step_handler(msg, self._guarded(self._process_photo_batch))
            return

        code = session.get("code", "unknown")
        try:
            photo_path = self._save_uploaded_photo(message, code)
        except Exception as e:
            msg = self.bot.reply_to(message, f"❌ Lưu ảnh thất bại: {e}\nGửi lại, hoặc gõ 'xong':")
            self.bot.register_next_step_handler(msg, self._guarded(self._process_photo_batch))
            return

        count = batch["count"] + 1
        session[f"{batch['prefix']}_{count}"] = photo_path
        batch["count"] = count

        if count >= batch["max"]:
            session.pop("_photo_batch", None)
            session.pop("_photo_batch_on_done", None)
            self.bot.reply_to(message, f"✅ Đã lưu ảnh {count}/{batch['max']} ({batch['label']}). Đủ số lượng tối đa.")
            on_done(chat_id)
            return

        session["_photo_batch"] = batch
        msg = self.bot.reply_to(
            message,
            f"✅ Đã lưu ảnh {count}/{batch['max']} ({batch['label']}). Gửi thêm ảnh, hoặc gõ 'xong':"
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_photo_batch))

    # ==================================================================
    # Routing callback
    # ==================================================================

    ADD_FLOW_ACTIONS = ("addboard", "devicesearch", "devicenew", "addcontractor", "addtest", "addafter", "addstation")
    DETAIL_FLOW_ACTIONS = ("detailboard", "detaildevice", "detailrecord")
    UPDATE_FLOW_ACTIONS = (
        "updateboard", "updaterecord", "updatefield",
        "updatetest", "updateafter", "updatestation", "updatecontractor", "updatedone",
    )

    def _route_callback(self, call):
        chat_id = call.message.chat.id
        parts = call.data.split(":")  # ["repaircrud", "<action>", ...]
        action = parts[1] if len(parts) > 1 else None

        if action == "cancel":
            self._add_sessions.pop(chat_id, None)
            self._detail_sessions.pop(chat_id, None)
            self._update_sessions.pop(chat_id, None)
            self.bot.send_message(chat_id, "❎ Đã hủy.")
            return

        if action in self.ADD_FLOW_ACTIONS and chat_id not in self._add_sessions:
            # Session mất (server restart, hết hạn, hoặc bấm nhầm nút của 1 phiên
            # /repair add cũ đã kết thúc/hủy trước đó) -> báo rõ thay vì KeyError.
            self.bot.send_message(
                chat_id,
                "⚠️ Phiên thêm Repair đã hết hạn hoặc không còn tồn tại "
                "(có thể do server vừa khởi động lại, hoặc bạn bấm nhầm nút của 1 phiên cũ).\n"
                "Gõ /repair add để bắt đầu lại."
            )
            return

        if action in self.UPDATE_FLOW_ACTIONS and chat_id not in self._update_sessions:
            self.bot.send_message(
                chat_id,
                "⚠️ Phiên cập nhật Repair đã hết hạn hoặc không còn tồn tại.\n"
                "Gõ /repair update để bắt đầu lại."
            )
            return

        if action in ("add", "search", "detail", "update"):
            self._dispatch(action, chat_id)
        elif action == "addboard":
            self._handle_add_board_selected(chat_id, int(parts[2]))
        elif action == "devicesearch":
            self._handle_device_search_button(chat_id)
        elif action == "devicenew":
            self._handle_device_new_button(chat_id)
        elif action == "addcontractor":
            self._handle_add_contractor_selected(chat_id, int(parts[2]))
        elif action == "addtest":
            self._handle_add_test_selected(chat_id, parts[2])
        elif action == "addafter":
            self._handle_add_after_repair_selected(chat_id, parts[2])
        elif action == "addstation":
            self._handle_add_station_status_selected(chat_id, parts[2])
        elif action == "detailboard":
            self._handle_detail_board_selected(chat_id, int(parts[2]))
        elif action == "detaildevice":
            self._handle_detail_device_selected(chat_id, parts[2])
        elif action == "detailrecord":
            self._handle_detail_record_selected(chat_id, int(parts[2]))
        elif action == "updateboard":
            self._handle_update_board_selected(chat_id, int(parts[2]))
        elif action == "updaterecord":
            self._handle_update_record_selected(chat_id, int(parts[2]))
        elif action == "updatefield":
            self._handle_update_field_selected(chat_id, parts[2])
        elif action == "updatetest":
            self._handle_update_test_selected(chat_id, parts[2])
        elif action == "updateafter":
            self._handle_update_after_selected(chat_id, parts[2])
        elif action == "updatestation":
            self._handle_update_station_selected(chat_id, parts[2])
        elif action == "updatecontractor":
            self._handle_update_contractor_selected(chat_id, parts[2])
        elif action == "updatedone":
            self._handle_update_done(chat_id)

    # ==================================================================
    # Helpers dùng chung
    # ==================================================================

    @staticmethod
    def _parse_args(text):
        if not text:
            return []
        return text.strip().split()[1:]

    @staticmethod
    def _is_cancel(message):
        return bool(message.text) and message.text.strip().lower() == "/cancel"

    @staticmethod
    def _parse_id(text):
        try:
            return int((text or "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_enum(value):
        if not value:
            return None
        try:
            return TestResult(value)
        except ValueError:
            return None

    _DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")

    @classmethod
    def _parse_date(cls, text):
        for fmt in cls._DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date(), None
            except ValueError:
                continue
        return None, f"giá trị '{text}' không khớp định dạng ngày nào được hỗ trợ."