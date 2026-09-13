from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.user import User
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

class AddUserCommand(BaseCommand):
    def register(self):
        @self.bot.callback_query_handler(func=lambda call: call.data == "add_user_btn")
        @self.admin_only
        def callback_add(call):
            self.bot.answer_callback_query(call.id)

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("❌ Hủy", callback_data="cancel_add_user"))

            msg = self.bot.send_message(
                call.message.chat.id,
                "Mời sếp nhập **ID Telegram** cần THÊM (hoặc gõ /cancel để hủy):",
                parse_mode="Markdown",
                reply_markup=markup
            )
            self.bot.register_next_step_handler(msg, self.process_input)

        @self.bot.callback_query_handler(func=lambda call: call.data == "cancel_add_user")
        @self.admin_only
        def callback_cancel(call):
            self.bot.answer_callback_query(call.id)
            self.clear_pending_input(call.message)
            self.bot.send_message(call.message.chat.id, "❎ Đã hủy thao tác thêm user.")

    def process_input(self, message):
        # Bắt buộc check ở đây vì next_step_handler nuốt hết mọi tin nhắn,
        # kể cả command /cancel
        if message.text and message.text.strip().lower() == "/cancel":
            self.bot.reply_to(message, "❎ Đã hủy thao tác thêm user.")
            return

        telegram_id = message.chat.id
        with SessionLocal() as db:
            user = db.query(User).filter(User.telegram_id == telegram_id).first()
            if not user or user.role != "admin":
                self.bot.reply_to(message, "⛔ Lệnh này chỉ dành riêng cho **Admin**!", parse_mode="Markdown")
                return

        try:
            new_id = int(message.text.strip())
            with SessionLocal() as db:
                if db.query(User).filter(User.telegram_id == new_id).first():
                    self.bot.reply_to(message, "⚠️ Người dùng này đã tồn tại.")
                else:
                    db.add(User(telegram_id=new_id, full_name=f"User_{new_id}", role="user"))
                    db.commit()
                    self.bot.reply_to(message, f"✅ Đã thêm user ID: `{new_id}` thành công!", parse_mode="Markdown")
        except ValueError:
            self.bot.reply_to(message, "❌ Lỗi: ID chỉ được chứa số hoặc gõ /cancel để hủy!")