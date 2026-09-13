from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.bot.base_command import BaseCommand

class AdminCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['admin'],admin=True)
        def handle(message):    
            markup = InlineKeyboardMarkup()
            markup.add(
                InlineKeyboardButton("➕ Thêm User", callback_data="add_user_btn"),
                InlineKeyboardButton("❌ Xóa User", callback_data="del_user_btn")
            )
            self.bot.send_message(message.chat.id, "🛠 **BẢNG ĐIỀU KHIỂN ADMIN**\n\nChọn chức năng:", reply_markup=markup, parse_mode="Markdown")