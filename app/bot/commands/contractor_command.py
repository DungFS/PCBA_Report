# app/bot/commands/contractor_command.py
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.contractor import Contractor

CB_PREFIX = "contractorcrud"


class ContractorCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['contractor'], admin=True)
        def handle(message):
            args = self._parse_args(message.text)
            if not args:
                self._show_menu(message.chat.id)
                return
            self._dispatch(args[0].lower(), message.chat.id)

        @self.bot.callback_query_handler(
            func=lambda call: bool(call.data) and call.data.startswith(f"{CB_PREFIX}:")
        )
        @self.admin_only
        def handle_callback(call):
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                pass
            sub = call.data.split(":", 1)[1]
            self._dispatch(sub, call.message.chat.id)

    def _guarded(self, fn):
        """Bọc self.admin_only quanh 1 hàm sẽ được gọi lại bởi
        register_next_step_handler - vì đó là 1 lần gọi handler HOÀN TOÀN MỚI,
        không đi qua register_handler ban đầu, nên current_user_id (dùng cho
        AuditMixin) sẽ luôn là None nếu không set lại ở đây."""
        return self.admin_only(fn)

    def _dispatch(self, sub, chat_id):
        if sub == "list":
            self._list_contractors(chat_id)
        elif sub == "add":
            self._start_add(chat_id)
        elif sub == "edit":
            self._start_edit(chat_id)
        elif sub == "delete":
            self._start_delete(chat_id)
        else:
            self._show_help(chat_id, unknown=sub)

    # ------------------------------------------------------------------
    # Menu / help
    # ------------------------------------------------------------------

    def _show_menu(self, chat_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📋 Danh sách", callback_data=f"{CB_PREFIX}:list"),
            InlineKeyboardButton("➕ Thêm mới", callback_data=f"{CB_PREFIX}:add"),
            InlineKeyboardButton("✏️ Sửa", callback_data=f"{CB_PREFIX}:edit"),
            InlineKeyboardButton("🗑 Xóa", callback_data=f"{CB_PREFIX}:delete"),
        )
        self.bot.send_message(chat_id, "🔧 Quản lý Nhà thầu - chọn 1 thao tác:", reply_markup=markup)

    def _show_help(self, chat_id, unknown=None):
        lines = []
        if unknown:
            lines.append(f"❌ Không hiểu lệnh con '{unknown}'.\n")
        lines.append("📋 Các lệnh con của /contractor:")
        lines.append("  /contractor list    - xem danh sách nhà thầu")
        lines.append("  /contractor add     - thêm nhà thầu mới")
        lines.append("  /contractor edit    - sửa tên nhà thầu")
        lines.append("  /contractor delete  - xóa nhà thầu")
        self.bot.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------

    def _list_contractors(self, chat_id):
        with SessionLocal() as db:
            contractors = db.query(Contractor).order_by(Contractor.id).all()

        if not contractors:
            self.bot.send_message(chat_id, "📋 Chưa có nhà thầu nào.")
            return

        lines = ["📋 Danh sách nhà thầu:"] + [f"  #{c.id} - {c.name}" for c in contractors]
        self.bot.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # ADD
    # ------------------------------------------------------------------

    def _start_add(self, chat_id):
        msg = self.bot.send_message(chat_id, "➕ Nhập tên nhà thầu mới:\nGõ /cancel để hủy.")
        self.bot.register_next_step_handler(msg, self._guarded(self._process_add))

    def _process_add(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy thêm nhà thầu.")
            return

        name = (message.text or "").strip()
        if not name:
            self.bot.reply_to(message, "❌ Tên không được để trống.")
            return

        with SessionLocal() as db:
            try:
                c = Contractor(name=name)
                db.add(c)
                db.commit()
                db.refresh(c)
                cid = c.id
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Không thêm được nhà thầu: {e}")
                return

        self.bot.reply_to(message, f"✅ Đã thêm nhà thầu #{cid} - {name}")

    # ------------------------------------------------------------------
    # EDIT
    # ------------------------------------------------------------------

    def _start_edit(self, chat_id):
        with SessionLocal() as db:
            contractors = db.query(Contractor).order_by(Contractor.id).all()

        if not contractors:
            self.bot.send_message(chat_id, "📋 Chưa có nhà thầu nào để sửa.")
            return

        lines = ["📋 Danh sách nhà thầu:"] + [f"  #{c.id} - {c.name}" for c in contractors]
        text = "\n".join(lines) + "\n\n✏️ Nhập ID nhà thầu cần sửa:\nGõ /cancel để hủy."
        msg = self.bot.send_message(chat_id, text)
        self.bot.register_next_step_handler(msg, self._guarded(self._process_edit_get_id))

    def _process_edit_get_id(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy sửa nhà thầu.")
            return

        cid = self._parse_id(message.text)
        if cid is None:
            self.bot.reply_to(message, "❌ ID không hợp lệ, phải là số nguyên.")
            return

        with SessionLocal() as db:
            c = db.query(Contractor).filter(Contractor.id == cid).first()
            if not c:
                self.bot.reply_to(message, f"❌ Không tìm thấy nhà thầu #{cid}.")
                return
            current_name = c.name

        msg = self.bot.send_message(
            message.chat.id,
            f"Nhà thầu hiện tại: #{cid} - {current_name}\nNhập tên mới:\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_edit_apply), cid)

    def _process_edit_apply(self, message, cid):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy sửa nhà thầu.")
            return

        new_name = (message.text or "").strip()
        if not new_name:
            self.bot.reply_to(message, "❌ Tên không được để trống.")
            return

        with SessionLocal() as db:
            c = db.query(Contractor).filter(Contractor.id == cid).first()
            if not c:
                self.bot.reply_to(message, f"❌ Không tìm thấy nhà thầu #{cid} (có thể đã bị xóa).")
                return
            try:
                c.name = new_name
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Cập nhật thất bại: {e}")
                return

        self.bot.reply_to(message, f"✅ Đã cập nhật nhà thầu #{cid} -> {new_name}")

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------

    def _start_delete(self, chat_id):
        with SessionLocal() as db:
            contractors = db.query(Contractor).order_by(Contractor.id).all()

        if not contractors:
            self.bot.send_message(chat_id, "📋 Chưa có nhà thầu nào để xóa.")
            return

        lines = ["📋 Danh sách nhà thầu:"] + [f"  #{c.id} - {c.name}" for c in contractors]
        text = "\n".join(lines) + "\n\n🗑 Nhập ID nhà thầu cần xóa:\nGõ /cancel để hủy."
        msg = self.bot.send_message(chat_id, text)
        self.bot.register_next_step_handler(msg, self._guarded(self._process_delete_get_id))

    def _process_delete_get_id(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy xóa nhà thầu.")
            return

        cid = self._parse_id(message.text)
        if cid is None:
            self.bot.reply_to(message, "❌ ID không hợp lệ, phải là số nguyên.")
            return

        with SessionLocal() as db:
            c = db.query(Contractor).filter(Contractor.id == cid).first()
            if not c:
                self.bot.reply_to(message, f"❌ Không tìm thấy nhà thầu #{cid}.")
                return
            name = c.name

        msg = self.bot.send_message(
            message.chat.id,
            f"⚠️ Bạn chắc chắn muốn xóa nhà thầu #{cid} - {name}?\n"
            f"Gõ chính xác XOA để xác nhận, hoặc /cancel để hủy.\n\n"
            f"Lưu ý: các repair đang gắn nhà thầu này sẽ tự chuyển về 'không rõ nhà thầu' (contractor_id = NULL)."
        )
        self.bot.register_next_step_handler(msg, self._guarded(self._process_delete_confirm), cid)

    def _process_delete_confirm(self, message, cid):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy xóa nhà thầu.")
            return

        if (message.text or "").strip() != "XOA":
            self.bot.reply_to(message, "❎ Không khớp xác nhận, đã hủy xóa nhà thầu.")
            return

        with SessionLocal() as db:
            c = db.query(Contractor).filter(Contractor.id == cid).first()
            if not c:
                self.bot.reply_to(message, f"❌ Không tìm thấy nhà thầu #{cid} (có thể đã bị xóa trước đó).")
                return
            try:
                db.delete(c)
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Xóa thất bại: {e}")
                return

        self.bot.reply_to(message, f"✅ Đã xóa nhà thầu #{cid}.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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