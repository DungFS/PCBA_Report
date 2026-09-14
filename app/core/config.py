import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
# Model embedding CHẠY LOCAL (không cần API key) cho tính năng RAG trong
# /ask - xem app/services/embedding_service.py. Đổi biến này nếu muốn dùng
# model khác (vd bản "-base"/"-large" để tăng độ chính xác).
EMBEDDING_MODEL_NAME = os.getenv('EMBEDDING_MODEL_NAME', 'intfloat/multilingual-e5-small')