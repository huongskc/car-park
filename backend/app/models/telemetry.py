from sqlalchemy import Column, BigInteger, Integer, String, Boolean, DateTime, Numeric, ForeignKey, func
from app.models.base import Base


class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    device_id = Column(String(50), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    slot_id = Column(String(20), nullable=False)
    distance_cm = Column(Numeric(5, 2), nullable=False)
    is_occupied = Column(Boolean, nullable=False)
    boot_id = Column(String(50), nullable=True)
    seq = Column(Integer, nullable=True)
    sampled_at = Column(DateTime, nullable=False)
    received_at = Column(DateTime, nullable=False, server_default=func.now())
