from sqlalchemy import Column, Integer, String, DateTime, Numeric, Enum, ForeignKey, func
from app.models.base import Base
from app.models.enums import PaymentMethod, PaymentStatus


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("parking_sessions.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=False, default=PaymentMethod.CASH)
    payment_status = Column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.PAID)
    transaction_code = Column(String(50), nullable=True, unique=True)
    paid_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    operator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
