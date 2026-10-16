from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric, Enum, ForeignKey, func
from app.models.base import Base, TimestampMixin
from app.models.enums import SessionStatus


class ParkingSession(Base, TimestampMixin):
    __tablename__ = "parking_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_code = Column(String(50), nullable=False, unique=True)
    card_uid = Column(String(50), ForeignKey("cards.uid", ondelete="RESTRICT"), nullable=False, index=True)
    slot_id = Column(String(20), ForeignKey("slots.id", ondelete="SET NULL"), nullable=True)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False)
    status = Column(Enum(SessionStatus), nullable=False, default=SessionStatus.ACTIVE, index=True)
    entry_time = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    entry_plate = Column(String(20), nullable=True)
    entry_image_url = Column(String(255), nullable=True)
    exit_time = Column(DateTime, nullable=True)
    exit_plate = Column(String(20), nullable=True)
    exit_image_url = Column(String(255), nullable=True)
    fee_amount = Column(Numeric(10, 2), nullable=False, default=20000.00)
    is_paid = Column(Boolean, nullable=False, default=False)
    review_reason = Column(String(255), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
