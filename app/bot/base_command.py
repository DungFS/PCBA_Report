from abc import ABC, abstractmethod
from app.core.config import ADMIN_ID
from app.bot.middlewares import auth_required, admin_required


class BaseCommand(ABC):
    def __init__(self, bot):
        self.bot = bot
        self.admin_id = ADMIN_ID
        self.auth = auth_required(bot)
        self.admin_only = admin_required(bot)

    @abstractmethod
    def register(self):
        """Subclass override method này, nhưng nên gọi qua self.register_handler(...)
        thay vì tự bot.message_handler(...) trực tiếp."""
        pass

    def register_handler(self, *, commands=None, public=False, admin=False, **filters):
        """
        Dùng như decorator:

            @self.register_handler(commands=['start'], public=True)
            def handle(message):
                ...
        """
        if public and admin:
            raise ValueError("Không thể vừa public=True vừa admin=True")

        def decorator(func):
            if public:
                wrapped = func
            elif admin:
                wrapped = self.admin_only(func)
            else:
                wrapped = self.auth(func)

            self.bot.message_handler(commands=commands, **filters)(wrapped)
            return func  # trả về hàm gốc (chưa wrap) để có thể gọi trực tiếp nếu cần

        return decorator

    def clear_pending_input(self, message):
        """Hủy next_step_handler đang treo cho chat này (nếu có)."""
        self.bot.clear_step_handler_by_chat_id(chat_id=message.chat.id)