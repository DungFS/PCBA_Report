from app.bot.base_command import BaseCommand
from app.core.database import SessionLocal
from app.models.user import User

class StartCommand(BaseCommand):
    def register(self):
        @self.register_handler(commands=['start'], public=True)
        def handle(message):
            telegram_id = message.chat.id
            first_name = message.from_user.first_name or ""
            last_name = message.from_user.last_name or ""
            full_name = f"{first_name} {last_name}".strip() or "No Name"

            with SessionLocal() as db:
                user = db.query(User).filter(User.telegram_id == telegram_id).first()
                user.full_name = full_name
                db.commit()

            self.bot.reply_to(message, f"✅ Chào mừng **{full_name}** đã quay lại hệ thống PCBA!", parse_mode="Markdown")