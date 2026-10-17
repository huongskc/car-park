from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric, Enum, ForeignKey, func
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin
from app.models.enums import DeviceStatus


class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    id = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    site_id = Column(String(50), nullable=False, default="PTIT_ZONE_1", index=True)
    auth_token = Column(String(255), nullable=False)
    status = Column(Enum(DeviceStatus), nullable=False, default=DeviceStatus.REGISTERED, index=True)
    is_online = Column(Boolean, nullable=False, default=False, index=True)
    ip_address = Column(String(45), nullable=True)
    fw_version = Column(String(20), nullable=True, default="1.0.0")
    hw_version = Column(String(50), nullable=True, default="ESP32-WROOM-32")
    last_heartbeat_at = Column(DateTime, nullable=True)

    config = relationship("DeviceConfig", back_populates="device", uselist=False, lazy="joined", cascade="all, delete-orphan")


class DeviceConfig(Base):
    __tablename__ = "device_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True)
    distance_threshold_cm = Column(Numeric(5, 2), nullable=False, default=15.00)
    servo_open_time_sec = Column(Integer, nullable=False, default=10)
    heartbeat_interval_sec = Column(Integer, nullable=False, default=10)
    buzzer_enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    device = relationship("Device", back_populates="config")
