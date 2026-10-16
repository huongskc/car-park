from sqlalchemy import Column, Integer, String, Boolean, Enum
from app.models.base import Base, TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), nullable=True, unique=True)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.VIEWER, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
