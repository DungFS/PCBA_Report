from sqlalchemy import Column, Integer, String, BigInteger
from app.core.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    full_name = Column(String(255), nullable=True) # Lưu tên người dùng
    role = Column(String(50), default="user")