# app/models/repair_embedding.py
from sqlalchemy import Column, Integer, ForeignKey, Text, String, JSON, DateTime, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class RepairEmbedding(Base):
    """Vector embedding (RAG) của từng Repair - dùng để tìm các lỗi TƯƠNG TỰ
    NHAU theo ngữ nghĩa (khác với so khớp từ khoá bằng LIKE trong SQL), xem
    app/services/rag_service.py.

    Mỗi Repair có tối đa 1 dòng ở đây (repair_id unique) - được tạo/ghi đè
    (re-index) mỗi khi repair được tạo mới hoặc sửa các field mô tả lỗi
    (board_code, original_phenomenon, failure_cause, disposition)."""

    __tablename__ = "repair_embeddings"
    __table_args__ = {"comment": "Vector embedding của repair, phục vụ tìm lỗi tương tự theo ngữ nghĩa (RAG)"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    repair_id = Column(
        Integer, ForeignKey("repairs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
        comment="1-1 với repairs.id - xoá kèm khi repair bị xoá"
    )
    source_text = Column(
        Text, nullable=False,
        comment="Văn bản gốc đã được embed (board + hiện tượng + nguyên nhân + cách sửa), lưu lại để debug/biết khi nào cần re-index"
    )
    embedding_model = Column(
        String(100), nullable=False,
        comment="Tên model embedding đã dùng (vd voyage-3.5) - đổi model cần re-index lại toàn bộ vì vector không tương thích chéo model"
    )
    embedding = Column(
        JSON, nullable=False,
        comment="Vector embedding, lưu dạng JSON list[float]"
    )

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    repair = relationship("Repair", backref="embedding_row")

    def __repr__(self):
        return f"<RepairEmbedding repair_id={self.repair_id} model={self.embedding_model}>"
