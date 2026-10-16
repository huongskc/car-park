from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, func
from app.models.base import Base
from app.models.enums import AlertType, AlertSeverity, AlertStatus


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(Enum(AlertType), nullable=False)
    severity = Column(Enum(AlertSeverity), nullable=False, default=AlertSeverity.WARNING, index=True)
    message = Column(String(255), nullable=False)
    device_id = Column(String(50), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    session_id = Column(Integer, ForeignKey("parking_sessions.id", ondelete="SET NULL"), nullable=True)
    status = Column(Enum(AlertStatus), nullable=False, default=AlertStatus.NEW, index=True)
    resolved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    resolved_at = Column(DateTime, nullable=True)
