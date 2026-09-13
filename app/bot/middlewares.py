from functools import wraps
from telebot.types import Message, CallbackQuery
from app.core.database import SessionLocal
from app.models.user import User
from app.core.audit_context import current_user_id

def _extract_telegram_id(obj) -> int:
    """Lấy telegram_id dù obj là Message hay CallbackQuery."""
    if isinstance(obj, CallbackQuery):
        return obj.from_user.id
    if isinstance(obj, Message):
        return obj.chat.id
    raise TypeError(f"Không hỗ trợ kiểu đối tượng: {type(obj)}")


def _deny(bot, obj, text: str):
    """Trả lời từ chối, tự chọn cách phản hồi phù hợp với Message hay CallbackQuery."""
    if isinstance(obj, CallbackQuery):
        # Callback không dùng reply_to được -> answer_callback_query (popup)
        # + gửi thêm message vào chat cho rõ ràng (vì alert popup dễ bị bỏ qua)
        bot.answer_callback_query(obj.id, text=text, show_alert=True)
        bot.send_message(obj.message.chat.id, text, parse_mode="Markdown")
    else:
        bot.reply_to(obj, text, parse_mode="Markdown")


def auth_required(bot):
    """Decorator: Bắt buộc user phải có trong Database (Đã được cấp quyền) mới được dùng.
    Dùng được cho cả message_handler và callback_query_handler."""
    def decorator(func):
        @wraps(func)
        def wrapper(obj, *args, **kwargs):
            telegram_id = _extract_telegram_id(obj)

            with SessionLocal() as db:
                user = db.query(User).filter(User.telegram_id == telegram_id).first()

            if not user:
                _deny(
                    bot, obj,
                    f"⛔ **BẠN CHƯA CÓ QUYỀN TRUY CẬP**\n\nID của bạn là: `{telegram_id}`\nHãy gửi ID này cho Admin để được cấp quyền."
                )
                return
            
            token = current_user_id.set(user.id)
            try:
                return func(obj, *args, **kwargs)
            finally:
                current_user_id.reset(token)
        return wrapper
    return decorator


def admin_required(bot):
    """Decorator: Bắt buộc user phải tồn tại trong DB và có role == 'admin'.
    Dùng được cho cả message_handler và callback_query_handler.

    Cũng set current_user_id giống auth_required - nếu không set, mọi thao tác
    tạo/sửa dữ liệu qua các command admin=True (board, repair, command_board...)
    sẽ có created_by_id/updated_by_id luôn None, vì AuditMixin đọc giá trị user
    hiện tại từ chính contextvar này (xem app/models/mixins.py)."""
    def decorator(func):
        @wraps(func)
        def wrapper(obj, *args, **kwargs):
            telegram_id = _extract_telegram_id(obj)

            with SessionLocal() as db:
                user = db.query(User).filter(User.telegram_id == telegram_id).first()

            if not user or user.role != "admin":
                _deny(bot, obj, "⛔ Lệnh này chỉ dành riêng cho **Admin**!")
                return

            token = current_user_id.set(user.id)
            try:
                return func(obj, *args, **kwargs)
            finally:
                current_user_id.reset(token)
        return wrapper
    return decorator