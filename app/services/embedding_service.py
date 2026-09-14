# app/services/embedding_service.py
from app.core.config import VOYAGE_API_KEY

"""
Service kết nối tới Voyage AI (https://www.voyageai.com) - nhà cung cấp
embedding được Anthropic khuyến nghị (Anthropic không tự có model embedding
riêng). Dùng cho tính năng RAG (tìm lỗi tương tự theo ngữ nghĩa) trong
app/services/rag_service.py.

Đây là service TÁCH RIÊNG khỏi ClaudeService (app/services/claude_service.py)
vì phục vụ 2 việc khác nhau:
    - ClaudeService  : gọi model NGÔN NGỮ (qua OpenRouter) để sinh SQL / tổng
                        hợp câu trả lời tự nhiên.
    - EmbeddingService: gọi model EMBEDDING (qua Voyage AI trực tiếp) để biến
                        văn bản thành vector số, phục vụ so khớp ngữ nghĩa
                        (cosine similarity) - việc mà model ngôn ngữ chat
                        thông thường không làm được.

Cấu hình:
    Đọc API key từ biến môi trường VOYAGE_API_KEY - KHÔNG hardcode key vào
    code. Đặt trong file .env hoặc export trực tiếp trước khi chạy bot.
    Lấy key tại: https://www.voyageai.com (mục API keys).

Class này CHỈ lo việc gọi API (build request, gửi, nhận vector). Logic
nghiệp vụ (build text để embed, lưu/truy vấn vector, tính similarity) nằm ở
app/services/rag_service.py.
"""
from typing import List, Optional

import requests

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
# voyage-3.5: model embedding đa ngôn ngữ hiện tại của Voyage AI, hỗ trợ tốt
# tiếng Việt. Xem thêm model khác tại https://docs.voyageai.com/docs/embeddings
DEFAULT_MODEL = "voyage-3.5"
DEFAULT_TIMEOUT_SECONDS = 30


class EmbeddingService:
    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        key = api_key or VOYAGE_API_KEY
        if not key:
            raise RuntimeError(
                "Thiếu VOYAGE_API_KEY. Đặt biến môi trường VOYAGE_API_KEY trước "
                "khi khởi tạo EmbeddingService (vd trong file .env). Lấy key tại "
                "https://www.voyageai.com"
            )
        self.api_key = key
        self.model = model

    def embed(self, texts: List[str], input_type: Optional[str] = "document") -> List[List[float]]:
        """Sinh embedding cho danh sách văn bản.

        input_type="document" khi embed dữ liệu cần lưu trữ (repair đã có
        trong DB), "query" khi embed câu hỏi cần tìm kiếm - Voyage tối ưu
        vector khác nhau tuỳ loại này để tăng độ chính xác so khớp; dùng sai
        loại vẫn chạy được nhưng độ chính xác giảm.
        """
        if not texts:
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"input": texts, "model": self.model}
        if input_type:
            payload["input_type"] = input_type

        try:
            resp = requests.post(
                VOYAGE_API_URL, headers=headers, json=payload,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Không kết nối được tới Voyage AI: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"Voyage AI API lỗi (status {resp.status_code}): {resp.text[:500]}"
            )

        data = resp.json()
        try:
            # Sắp lại theo "index" cho chắc - API không đảm bảo giữ nguyên thứ tự input.
            items = sorted(data["data"], key=lambda d: d["index"])
            return [item["embedding"] for item in items]
        except (KeyError, TypeError) as e:
            raise RuntimeError(
                f"Response từ Voyage AI không đúng định dạng mong đợi: {data}"
            ) from e


_default_instance: Optional["EmbeddingService"] = None


def get_embedding_service() -> EmbeddingService:
    """Singleton đơn giản - tránh validate lại key mỗi lần gọi. Các nơi khác
    trong code nên dùng hàm này thay vì tự `EmbeddingService()` trực tiếp."""
    global _default_instance
    if _default_instance is None:
        _default_instance = EmbeddingService()
    return _default_instance
