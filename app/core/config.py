import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
# Dùng cho tính năng RAG (tìm lỗi tương tự theo ngữ nghĩa) trong /ask - xem
# app/services/embedding_service.py. Voyage AI là nhà cung cấp embedding
# được Anthropic khuyến nghị (Anthropic không có model embedding riêng).
VOYAGE_API_KEY = os.getenv('VOYAGE_API_KEY')