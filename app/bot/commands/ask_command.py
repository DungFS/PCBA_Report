# app/bot/commands/ask_command.py
"""
Lệnh /ask - CHỈ ADMIN được dùng. Cho phép hỏi đáp tự nhiên (tiếng Việt) về
dữ liệu trong DB: đếm/liệt kê theo user, board, nhà thầu; tìm các lỗi có mô
tả tương tự nhau; đề xuất cách sửa chữa dựa trên lịch sử đã sửa thành công...

Cách dùng:
    /ask User A đã sửa được bao nhiêu board trong tháng này?
    /ask Liệt kê các lỗi tương tự lỗi "hỏng tụ C46"
    /ask Đề xuất cách sửa cho các board có hiện tượng giống board OCPP...KWA

Nếu gõ /ask không kèm câu hỏi, bot sẽ hỏi lại ở tin nhắn tiếp theo.

Xử lý nghiệp vụ (sinh SQL, chạy SQL, tổng hợp câu trả lời) nằm ở
app/services/nl_query_service.py - file này chỉ lo phần giao tiếp Telegram.
"""
from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.user import User
from app.services.nl_query_service import NLQueryError, answer_question

# Để dư an toàn so với giới hạn 4096 ký tự/tin nhắn của Telegram.
MAX_MESSAGE_LEN = 3500


class AskCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['ask'], admin=True)
        def handle(message):
            question = self._extract_question(message.text)
            if question:
                self._process(message, question)
            else:
                msg = self.bot.reply_to(
                    message,
                    "🤖 Mời sếp nhập câu hỏi về dữ liệu hệ thống "
                    "(vd: \"User A đã sửa được bao nhiêu board?\"), "
                    "hoặc gõ /cancel để hủy:",
                )
                self.bot.register_next_step_handler(msg, self._handle_next_step)

    def _handle_next_step(self, message):
        # next_step_handler nuốt hết mọi tin nhắn kế tiếp trong chat này, kể
        # cả /cancel -> phải tự bắt và tự re-check quyền admin ở đây (giống
        # cách AddUserCommand.process_input đang làm).
        if message.text and message.text.strip().lower() == "/cancel":
            self.bot.reply_to(message, "❎ Đã hủy.")
            return

        with SessionLocal() as db:
            user = db.query(User).filter(User.telegram_id == message.chat.id).first()
        if not user or user.role != "admin":
            self.bot.reply_to(message, "⛔ Lệnh này chỉ dành riêng cho **Admin**!", parse_mode="Markdown")
            return

        question = (message.text or "").strip()
        if not question:
            self.bot.reply_to(message, "❌ Câu hỏi trống, thao tác đã hủy.")
            return
        self._process(message, question)

    @staticmethod
    def _extract_question(text: str) -> str:
        parts = (text or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    def _process(self, message, question: str):
        status_msg = self.bot.reply_to(message, "🤖 Đang phân tích dữ liệu, vui lòng đợi...")

        try:
            result = answer_question(question)
        except NLQueryError as e:
            self._safe_send(message.chat.id, f"⚠️ Không thể xử lý câu hỏi này:\n{e}")
            return
        except Exception as e:
            self._safe_send(message.chat.id, f"❌ Có lỗi xảy ra khi xử lý câu hỏi: {e}")
            return
        finally:
            try:
                self.bot.delete_message(status_msg.chat.id, status_msg.message_id)
            except Exception:
                pass

        answer_text = result.answer_text.strip() or "(AI không trả về nội dung)"
        header = f"🤖 KẾT QUẢ ({result.row_count} dòng dữ liệu liên quan):\n\n"
        for chunk in self._chunk_text(header + answer_text, MAX_MESSAGE_LEN):
            self._safe_send(message.chat.id, chunk)

        # Gửi kèm câu SQL đã dùng để Admin có thể tự kiểm tra lại nếu cần.
        self._safe_send(
            message.chat.id,
            f"🔎 SQL đã dùng:\n```sql\n{result.sql}\n```",
            parse_mode="Markdown",
        )

    def _safe_send(self, chat_id, text, parse_mode=None):
        """Gửi tin nhắn, tự fallback về plain text nếu parse_mode làm
        Telegram lỗi (vd nội dung AI sinh ra chứa ký tự Markdown không hợp lệ)."""
        try:
            self.bot.send_message(chat_id, text, parse_mode=parse_mode)
        except Exception:
            self.bot.send_message(chat_id, text)

    @staticmethod
    def _chunk_text(text: str, size: int):
        for i in range(0, len(text), size):
            yield text[i:i + size]
