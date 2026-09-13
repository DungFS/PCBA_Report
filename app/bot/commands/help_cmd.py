from app.bot.base_command import BaseCommand


class HelpCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['help'])
        def handle(message):
            help_text = (
                "📖 *HƯỚNG DẪN SỬ DỤNG HỆ THỐNG PCBA*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "👤 *LỆNH CHUNG*\n"
                "▸ /start — Xác thực tài khoản\n"
                "▸ /help — Xem hướng dẫn này\n\n"
                "🛠 *LỆNH DÀNH CHO ADMIN*\n"
                "▸ /admin — Mở bảng điều khiển quản lý user\n\n"
                "   ➕ *Thêm User*\n"
                "   Nhập ID Telegram cần cấp quyền.\n"
                "   ↳ Gõ /cancel hoặc bấm ❌ Hủy để dừng lại.\n\n"
                "   ❌ *Xóa User*\n"
                "   Chọn user từ danh sách để xóa.\n"
                "   ↳ Bấm ❌ Hủy để đóng danh sách.\n\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            self.bot.reply_to(message, help_text, parse_mode="Markdown")