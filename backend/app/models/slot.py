from sqlalchemy import Column, String, DateTime, Numeric, Enum, ForeignKey, func
from app.models.base import Base
from app.models.enums import SlotStatus


class Slot(Base):
    __tablename__ = "slots"

    id = Column(String(20), primary_key=True)
    name = Column(String(50), nullable=False)
    site_id = Column(String(50), nullable=False, default="PTIT_ZONE_1", index=True)
    device_id = Column(String(50), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    status = Column(Enum(SlotStatus), nullable=False, default=SlotStatus.EMPTY, index=True)
    last_distance_cm = Column(Numeric(5, 2), nullable=True)
    last_updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
