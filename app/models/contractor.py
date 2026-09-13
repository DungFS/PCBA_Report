from sqlalchemy import Column, Integer, String
from app.core.database import Base
from app.models.mixins import AuditMixin


class Contractor(Base, AuditMixin):
    __tablename__ = "contractors"
    __table_args__ = {"comment": "Danh mục nhà thầu thực hiện sửa chữa"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(
        String(255), nullable=False,
        comment="Tên nhà thầu"
    )

    def __repr__(self):
        return f"<Contractor id={self.id} name={self.name}>"