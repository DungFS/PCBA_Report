from sqlalchemy import Column, Integer, String
from app.core.database import Base
from app.models.mixins import AuditMixin


class Board(Base, AuditMixin):
    __tablename__ = "boards"
    __table_args__ = {"comment": "Danh mục các loại board (model board)"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    code = Column(
        String(100), nullable=False, unique=True, index=True,
        comment="Mã định danh loại board, vd OCPP_Three_V2.6_22KW"
    )
    ten = Column(
        String(255), nullable=True,
        comment="Tên hiển thị của loại board"
    )

    def __repr__(self):
        return f"<Board id={self.id} code={self.code} ten={self.ten}>"