# app/bot/commands_setup.py
from telebot.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from app.core.database import SessionLocal
from app.models.user import User

DEFAULT_COMMANDS = [
    BotCommand("start", "Khởi động lại bot"),
    BotCommand("help", "Xem hướng dẫn sử dụng"),
    BotCommand("repair","Quan ly repair"),
    BotCommand("user_report", "Báo cáo tổng hợp theo User (theo tuần)"),
    BotCommand("contractor_report", "Báo cáo mức độ sử dụng theo nhà thầu")
]

ADMIN_COMMANDS = DEFAULT_COMMANDS + [
    BotCommand("admin", "Mở bảng điều khiển admin"),
    BotCommand("board", "Quản lý board (list/add/edit/delete)"),
    BotCommand("contractor","Quản lý nha thau"),
    BotCommand("repair_detail_report", "Báo cáo chi tiết Repair"),
    BotCommand("report_daily", "Báo cáo hang ngay")
    # import_repair đã chuyển sang chạy qua terminal (./run_import.sh),
    # không còn là command trên Telegram nữa.
]


def setup_default_commands(bot):
    """Gọi 1 lần lúc khởi động app."""
    bot.set_my_commands(DEFAULT_COMMANDS, scope=BotCommandScopeDefault())

    with SessionLocal() as db:
        admins = db.query(User).filter(User.role == "admin").all()
        for admin in admins:
            _set_admin_menu(bot, admin.telegram_id)


def _set_admin_menu(bot, telegram_id: int):
    bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=telegram_id))


def _reset_to_default_menu(bot, telegram_id: int):
    """Dùng khi 1 user bị xóa hoặc bị hạ quyền admin -> trả về menu mặc định."""
    bot.set_my_commands(DEFAULT_COMMANDS, scope=BotCommandScopeChat(chat_id=telegram_id))


def refresh_menu_for_user(bot, telegram_id: int, is_admin: bool):
    """Gọi mỗi khi có thay đổi role/xóa user, để cập nhật menu đúng ngay lập tức."""
    if is_admin:
        _set_admin_menu(bot, telegram_id)
    else:
        _reset_to_default_menu(bot, telegram_id)