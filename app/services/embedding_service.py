# app/services/embedding_service.py
"""
Service sinh embedding (vector) CHẠY LOCAL trên chính server của bot, dùng
model đa ngôn ngữ từ thư viện `sentence-transformers` (Hugging Face) - không
cần API key, không tốn phí theo lượt gọi (khác với dùng 1 dịch vụ embedding
trả phí như Voyage AI/OpenAI). Dùng cho tính năng RAG (tìm lỗi tương tự theo
ngữ nghĩa) trong app/services/rag_service.py.

Đây là service TÁCH RIÊNG khỏi ClaudeService (app/services/claude_service.py)
vì phục vụ 2 việc khác nhau:
    - ClaudeService  : gọi model NGÔN NGỮ (qua OpenRouter) để sinh SQL / tổng
                        hợp câu trả lời tự nhiên.
    - EmbeddingService: chạy model EMBEDDING NGAY TRÊN SERVER để biến văn bản
                        thành vector số, phục vụ so khớp ngữ nghĩa (cosine
                        similarity) - việc mà model ngôn ngữ chat thông
                        thường không làm được.

Đánh đổi so với dùng API embedding trả phí (vd Voyage AI):
    - KHÔNG tốn phí/không cần internet liên tục (chỉ cần internet 1 LẦN DUY
      NHẤT để tải model từ Hugging Face Hub về, sau đó model được cache lại
      ở ~/.cache/huggingface và chạy hoàn toàn offline).
    - Cần cài thêm `sentence-transformers` (kéo theo `torch`, nặng khoảng
      500MB-1GB tuỳ nền tảng) và tốn thêm RAM/CPU của server khi encode.
    - Model mặc định (intfloat/multilingual-e5-small) chạy tốt trên CPU,
      không bắt buộc GPU - phù hợp với quy mô dữ liệu của hệ thống này.

Model mặc định: intfloat/multilingual-e5-small - model embedding đa ngôn
ngữ nhỏ gọn (~470MB), hỗ trợ tốt tiếng Việt, có thể đổi qua biến môi trường
EMBEDDING_MODEL_NAME nếu cần model khác (vd bản "-base"/"-large" để tăng độ
chính xác, đánh đổi bằng tốc độ/RAM).

LƯU Ý (quy ước của họ model E5): văn bản cần được thêm tiền tố "query: "
(khi tìm kiếm) hoặc "passage: " (khi lưu trữ) trước khi encode - đây là quy
ước bắt buộc theo cách model này được huấn luyện, thiếu tiền tố vẫn chạy
được nhưng độ chính xác so khớp giảm rõ rệt. Class này tự thêm tiền tố dựa
vào `input_type`, nơi gọi không cần tự thêm.

Class này CHỈ lo việc sinh vector (load model, encode). Logic nghiệp vụ
(build text để embed, lưu/truy vấn vector, tính similarity) nằm ở
app/services/rag_service.py.
"""
import threading
from typing import List, Optional

from app.core.config import EMBEDDING_MODEL_NAME as DEFAULT_MODEL

# Tiền tố bắt buộc theo quy ước huấn luyện của model họ E5 (xem docstring trên).
_E5_PREFIX = {"document": "passage: ", "query": "query: "}


class EmbeddingService:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None  # lazy-load - xem _get_model()
        self._load_lock = threading.Lock()

    def _get_model(self):
        """Chỉ import `sentence_transformers` và tải model khi THỰC SỰ cần
        dùng (lần gọi embed() đầu tiên) - tránh làm chậm lúc khởi động bot
        hoặc tốn RAM cho các trường hợp không dùng tới /ask."""
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as e:
                    raise RuntimeError(
                        "Thiếu thư viện 'sentence-transformers'. Cài bằng: "
                        "pip install -r requirements.txt"
                    ) from e
                try:
                    self._model = SentenceTransformer(self.model_name)
                except Exception as e:
                    raise RuntimeError(
                        f"Không tải được model embedding '{self.model_name}'. "
                        "Lần chạy đầu cần internet để tải model từ Hugging Face "
                        f"Hub (~470MB cho model mặc định). Lỗi gốc: {e}"
                    ) from e
        return self._model

    @property
    def model(self) -> str:
        """Tên model - dùng để lưu kèm mỗi embedding (xem RepairEmbedding),
        biết khi nào cần re-index lại toàn bộ nếu đổi model."""
        return self.model_name

    def embed(self, texts: List[str], input_type: Optional[str] = "document") -> List[List[float]]:
        """Sinh embedding cho danh sách văn bản.

        input_type="document" khi embed dữ liệu cần lưu trữ (repair đã có
        trong DB), "query" khi embed câu hỏi cần tìm kiếm - model E5 tối ưu
        vector khác nhau tuỳ loại này (qua tiền tố _E5_PREFIX) để tăng độ
        chính xác so khớp.
        """
        if not texts:
            return []

        prefix = _E5_PREFIX.get(input_type, "")
        prefixed = [f"{prefix}{t}" for t in texts]

        model = self._get_model()
        try:
            vectors = model.encode(prefixed, normalize_embeddings=True)
        except Exception as e:
            raise RuntimeError(f"Sinh embedding local thất bại: {e}") from e

        return [v.tolist() for v in vectors]


_default_instance: Optional["EmbeddingService"] = None


def get_embedding_service() -> EmbeddingService:
    """Singleton đơn giản - tránh load lại model (tốn vài giây - vài chục
    giây) mỗi lần gọi. Các nơi khác trong code nên dùng hàm này thay vì tự
    `EmbeddingService()` trực tiếp."""
    global _default_instance
    if _default_instance is None:
        _default_instance = EmbeddingService()
    return _default_instance
