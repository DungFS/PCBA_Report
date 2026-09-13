import threading
import telebot
from fastapi import FastAPI

from app.core.config import BOT_TOKEN, ADMIN_ID
from app.core.database import engine, Base, SessionLocal
from app.models import User
from app.bot.router import register_handlers
from app.bot.commands_setup import setup_default_commands

# Khởi tạo Database
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    if not db.query(User).filter(User.telegram_id == ADMIN_ID).first():
        db.add(User(telegram_id=ADMIN_ID, role="admin"))
        db.commit()

# Khởi tạo Bot
bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()
register_handlers(bot)

# Cập nhật menu lệnh (mặc định cho tất cả + riêng cho từng admin)
setup_default_commands(bot)
print("Đã cập nhật Menu lệnh cho Bot.")

# Khởi tạo FastAPI
app = FastAPI(title="PCBA Repair System API")


def run_bot():
    print("Bot Telegram đang chạy...")
    bot.infinity_polling()


@app.on_event("startup")
def on_startup():
    threading.Thread(target=run_bot, daemon=True).start()


@app.get("/")
def root():
    return {"status": "Enterprise Structure is Running!"}