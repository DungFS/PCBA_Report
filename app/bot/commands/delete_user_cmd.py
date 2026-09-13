from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.user import User
from app.bot.commands_setup import refresh_menu_for_user


class DeleteUserCommand(BaseCommand):
    def register(self):
        @self.bot.callback_query_handler(func=lambda call: call.data == "del_user_btn")
        @self.admin_only
        def callback_del(call):
            self.bot.answer_callback_query(call.id)
            with SessionLocal() as db:
                users = db.query(User).filter(User.role != "admin").all()
                if not users:
                    self.bot.send_message(call.message.chat.id, "⚠️ Không có user nào khác ngoài Admin.")
                    return

                markup = InlineKeyboardMarkup()
                for u in users:
                    markup.add(InlineKeyboardButton(
                        f"👤 {u.full_name} ({u.telegram_id})",
                        callback_data=f"delete_{u.telegram_id}"
                    ))
                markup.add(InlineKeyboardButton("❌ Hủy", callback_data="cancel_del_user"))

                self.bot.send_message(
                    call.message.chat.id,
                    "📋 Chọn người dùng muốn XÓA:",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )

        @self.bot.callback_query_handler(func=lambda call: call.data == "cancel_del_user")
        @self.admin_only
        def callback_cancel(call):
            self.bot.answer_callback_query(call.id)
            try:
                self.bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            self.bot.send_message(call.message.chat.id, "❎ Đã hủy thao tác xóa user.")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("delete_"))
        @self.admin_only
        def confirm_delete(call):
            self.bot.answer_callback_query(call.id)
            target_id = int(call.data.split("_")[1])

            with SessionLocal() as db:
                user_to_delete = db.query(User).filter(User.telegram_id == target_id).first()

                if user_to_delete and user_to_delete.role == "admin":
                    self.bot.send_message(call.message.chat.id, "⛔ Không thể xóa tài khoản Admin.")
                    return

                if not user_to_delete:
                    self.bot.send_message(call.message.chat.id, "⚠️ User này không còn tồn tại.")
                    return

                name = user_to_delete.full_name
                db.delete(user_to_delete)
                db.commit()
                refresh_menu_for_user(self.bot, target_id, is_admin=False)
            try:
                self.bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass

            self.bot.send_message(
                call.message.chat.id,
                f"🗑 ĐÃ XÓA THÀNH CÔNG!\n- Tên: {name}\n- ID: {target_id}"
            )