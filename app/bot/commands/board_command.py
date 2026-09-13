# app/bot/commands/board_command.py
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.board import Board


class BoardCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['board'], admin=True)
        def handle(message):
            args = self._parse_args(message.text)
            if not args:
                self._show_menu(message.chat.id)
                return
            self._dispatch(args[0].lower(), message.chat.id)

        @self.bot.callback_query_handler(func=lambda call: bool(call.data) and call.data.startswith("board:"))
        def handle_callback(call):
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                pass  # không chặn xử lý nếu answer_callback_query lỗi (vd callback quá cũ)
            sub = call.data.split(":", 1)[1]
            self._dispatch(sub, call.message.chat.id)

    def _dispatch(self, sub, chat_id):
        if sub == "list":
            self._list_boards(chat_id)
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
            InlineKeyboardButton("📋 Danh sách", callback_data="board:list"),
            InlineKeyboardButton("➕ Thêm mới", callback_data="board:add"),
            InlineKeyboardButton("✏️ Sửa", callback_data="board:edit"),
            InlineKeyboardButton("🗑 Xóa", callback_data="board:delete"),
        )
        self.bot.send_message(chat_id, "🔧 Quản lý board - chọn 1 thao tác:", reply_markup=markup)

    def _show_help(self, chat_id, unknown=None):
        lines = []
        if unknown:
            lines.append(f"❌ Không hiểu lệnh con '{unknown}'.\n")
        lines.append("📋 Các lệnh con của /board:")
        lines.append("  /board list    - xem danh sách board")
        lines.append("  /board add     - thêm board mới")
        lines.append("  /board edit    - sửa board")
        lines.append("  /board delete  - xóa board")
        self.bot.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------

    def _list_boards(self, chat_id):
        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(chat_id, "📋 Chưa có board nào trong danh mục.")
            return

        self.bot.send_message(chat_id, self._format_board_list(boards))

    # ------------------------------------------------------------------
    # ADD (2 bước: code -> tên)
    # ------------------------------------------------------------------

    def _start_add(self, chat_id):
        msg = self.bot.send_message(
            chat_id,
            "➕ Nhập code cho board mới (vd: OCPP_Three_V2.6_22KW).\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._process_add_get_code)

    def _process_add_get_code(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy thêm board.")
            return

        code = (message.text or "").strip()
        if not code:
            self.bot.reply_to(message, "❌ Code không được để trống.")
            return

        msg = self.bot.send_message(
            message.chat.id,
            "Nhập tên hiển thị cho board (có thể để trống, gõ '-' nếu không có tên).\n"
            "Gõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._process_add_get_ten, code)

    def _process_add_get_ten(self, message, code):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy thêm board.")
            return

        ten = self._normalize_optional_text(message.text)

        with SessionLocal() as db:
            try:
                board = Board(code=code, ten=ten)
                db.add(board)
                db.commit()
                db.refresh(board)
                board_id = board.id
            except Exception as e:
                db.rollback()
                self.bot.reply_to(
                    message,
                    f"❌ Không thêm được board (có thể code '{code}' đã tồn tại): {e}"
                )
                return

        summary = f"✅ Đã thêm board #{board_id} - {code}"
        if ten:
            summary += f" ({ten})"
        self.bot.reply_to(message, summary)

    # ------------------------------------------------------------------
    # EDIT (hiện danh sách -> chọn ID -> code mới -> tên mới)
    # ------------------------------------------------------------------

    def _start_edit(self, chat_id):
        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(chat_id, "📋 Chưa có board nào để sửa.")
            return

        text = self._format_board_list(boards) + "\n\n✏️ Nhập ID board cần sửa:\nGõ /cancel để hủy."
        msg = self.bot.send_message(chat_id, text)
        self.bot.register_next_step_handler(msg, self._process_edit_get_id)

    def _process_edit_get_id(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy sửa board.")
            return

        board_id = self._parse_id(message.text)
        if board_id is None:
            self.bot.reply_to(message, "❌ ID không hợp lệ, phải là số nguyên.")
            return

        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.reply_to(message, f"❌ Không tìm thấy board có ID {board_id}.")
                return
            current_code, current_ten = board.code, board.ten

        msg = self.bot.send_message(
            message.chat.id,
            f"Board hiện tại: #{board_id} - {current_code}" +
            (f" ({current_ten})" if current_ten else "") + "\n"
            "Nhập code mới (Enter để giữ nguyên code cũ):\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(
            msg, self._process_edit_get_ten, board_id, current_code, current_ten
        )

    def _process_edit_get_ten(self, message, board_id, current_code, current_ten):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy sửa board.")
            return

        new_code_input = (message.text or "").strip()
        new_code = new_code_input if new_code_input else current_code

        msg = self.bot.send_message(
            message.chat.id,
            "Nhập tên mới (Enter để giữ nguyên, gõ '-' để xóa tên):\nGõ /cancel để hủy."
        )
        self.bot.register_next_step_handler(
            msg, self._process_edit_apply, board_id, new_code, current_ten
        )

    def _process_edit_apply(self, message, board_id, new_code, current_ten):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy sửa board.")
            return

        ten_input = message.text if message.text is not None else ""
        new_ten = current_ten if ten_input.strip() == "" else self._normalize_optional_text(ten_input)

        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.reply_to(
                    message,
                    f"❌ Không tìm thấy board có ID {board_id} (có thể đã bị xóa)."
                )
                return
            try:
                board.code = new_code
                board.ten = new_ten
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.reply_to(
                    message,
                    f"❌ Không cập nhật được (có thể code '{new_code}' đã tồn tại): {e}"
                )
                return

        summary = f"✅ Đã cập nhật board #{board_id} -> {new_code}"
        if new_ten:
            summary += f" ({new_ten})"
        self.bot.reply_to(message, summary)

    # ------------------------------------------------------------------
    # DELETE (hiện danh sách -> chọn ID -> xác nhận)
    # ------------------------------------------------------------------

    def _start_delete(self, chat_id):
        with SessionLocal() as db:
            boards = db.query(Board).order_by(Board.id).all()

        if not boards:
            self.bot.send_message(chat_id, "📋 Chưa có board nào để xóa.")
            return

        text = self._format_board_list(boards) + "\n\n🗑 Nhập ID board cần xóa:\nGõ /cancel để hủy."
        msg = self.bot.send_message(chat_id, text)
        self.bot.register_next_step_handler(msg, self._process_delete_get_id)

    def _process_delete_get_id(self, message):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy xóa board.")
            return

        board_id = self._parse_id(message.text)
        if board_id is None:
            self.bot.reply_to(message, "❌ ID không hợp lệ, phải là số nguyên.")
            return

        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.reply_to(message, f"❌ Không tìm thấy board có ID {board_id}.")
                return
            current_code, current_ten = board.code, board.ten

        label = current_code + (f" ({current_ten})" if current_ten else "")
        msg = self.bot.send_message(
            message.chat.id,
            f"⚠️ Bạn chắc chắn muốn xóa board #{board_id} - {label}?\n"
            f"Gõ chính xác XOA để xác nhận, hoặc /cancel để hủy."
        )
        self.bot.register_next_step_handler(msg, self._process_delete_confirm, board_id)

    def _process_delete_confirm(self, message, board_id):
        if self._is_cancel(message):
            self.bot.reply_to(message, "❎ Đã hủy xóa board.")
            return

        if (message.text or "").strip() != "XOA":
            self.bot.reply_to(message, "❎ Không khớp xác nhận, đã hủy xóa board.")
            return

        with SessionLocal() as db:
            board = db.query(Board).filter(Board.id == board_id).first()
            if not board:
                self.bot.reply_to(
                    message,
                    f"❌ Không tìm thấy board có ID {board_id} (có thể đã bị xóa trước đó)."
                )
                return
            try:
                db.delete(board)
                db.commit()
            except Exception as e:
                db.rollback()
                self.bot.reply_to(message, f"❌ Xóa thất bại: {e}")
                return

        self.bot.reply_to(message, f"✅ Đã xóa board #{board_id}.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_args(text):
        """'/board edit' -> ['edit']. '/board' hoặc None -> []."""
        if not text:
            return []
        parts = text.strip().split()
        return parts[1:]

    @staticmethod
    def _format_board_list(boards):
        lines = ["📋 Danh sách board:"] + [
            f"  #{b.id} - {b.code}" + (f" ({b.ten})" if b.ten else "")
            for b in boards
        ]
        return "\n".join(lines)

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
    def _normalize_optional_text(text):
        """'' hoặc '-' -> None (không có tên); ngược lại giữ nguyên text đã strip."""
        if text is None:
            return None
        stripped = text.strip()
        if not stripped or stripped == "-":
            return None
        return stripped