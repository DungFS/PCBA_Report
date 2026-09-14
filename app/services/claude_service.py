# app/services/claude_service.py
from app.core.config import OPENROUTER_API_KEY


"""
Service kết nối tới OpenRouter (https://openrouter.ai) - dùng chung cho các
tính năng AI trong bot (vd /ask tổng hợp câu trả lời từ dữ liệu RAG tìm
được, xem app/services/nl_query_service.py).

OpenRouter là 1 API GATEWAY duy nhất cho nhiều nhà cung cấp model (Anthropic,
OpenAI, Google, ...), dùng chung 1 format request kiểu OpenAI Chat Completions
- không cần cài SDK riêng của từng hãng, chỉ cần gọi HTTP thẳng.

Cấu hình:
    Đọc API key từ biến môi trường OPENROUTER_API_KEY - KHÔNG hardcode key
    vào code. Đặt trong file .env (nếu project đang load .env qua
    python-dotenv) hoặc export trực tiếp trước khi chạy bot:
        export OPENROUTER_API_KEY=sk-or-v1-...
    Lấy key tại: https://openrouter.ai/keys

Model:
    OpenRouter đặt tên model theo dạng "provider/model-id", vd:
    "anthropic/claude-sonnet-4.5", "openai/gpt-4o", "google/gemini-2.5-pro".
    Xem danh sách đầy đủ tại https://openrouter.ai/models
    Mặc định dùng Claude Sonnet - đổi DEFAULT_MODEL nếu muốn model khác.

Class này CHỈ lo việc gọi API (build request, gửi, nhận text response).
Logic nghiệp vụ (retrieval bằng RAG, build prompt, format kết quả...) nằm ở
service khác gọi vào đây - tách riêng để tái dùng được cho nhiều tính năng
AI khác nhau, không chỉ riêng /ask.
"""
import os
from typing import Optional

import requests

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_SECONDS = 60


class ClaudeService:
    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        key = api_key or OPENROUTER_API_KEY
        if not key:
            raise RuntimeError(
                "Thiếu OPENROUTER_API_KEY. Đặt biến môi trường OPENROUTER_API_KEY "
                "trước khi khởi tạo ClaudeService (vd trong file .env, hoặc "
                "export OPENROUTER_API_KEY=sk-or-v1-... trước khi chạy bot). "
                "Lấy key tại https://openrouter.ai/keys"
            )
        self.api_key = key
        self.model = model

    def ask(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> str:
        """
        Gửi 1 prompt đơn (1 user message duy nhất, không giữ lịch sử hội
        thoại - mỗi lần gọi độc lập), trả về text response đã gộp.

        temperature=0.0 mặc định vì mục đích chính hiện tại (sinh SQL) cần
        kết quả ổn định, ít "sáng tạo" - không phải chat tự do.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            resp = requests.post(
                OPENROUTER_API_URL, headers=headers, json=payload,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Không kết nối được tới OpenRouter: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenRouter API lỗi (status {resp.status_code}): {resp.text[:500]}"
            )

        return self._extract_text(resp.json())

    @staticmethod
    def _extract_text(data: dict) -> str:
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"Response từ OpenRouter không đúng định dạng mong đợi: {data}"
            ) from e


_default_instance: Optional["ClaudeService"] = None


def get_claude_service() -> ClaudeService:
    """Singleton đơn giản - tránh validate lại key mỗi lần gọi. Các nơi khác
    trong code nên dùng hàm này thay vì tự `ClaudeService()` trực tiếp."""
    global _default_instance
    if _default_instance is None:
        _default_instance = ClaudeService()
    return _default_instance