from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, func
from app.models.base import Base
from app.models.enums import GateId, CommandAction, CommandStatus


class Command(Base):
    __tablename__ = "commands"

    id = Column(String(50), primary_key=True)
    device_id = Column(String(50), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    gate_id = Column(Enum(GateId), nullable=False)
    action = Column(Enum(CommandAction), nullable=False)
    status = Column(Enum(CommandStatus), nullable=False, default=CommandStatus.PENDING)
    sent_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ack_status = Column(String(50), nullable=True)
    ack_reason = Column(String(255), nullable=True)
    sent_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    acked_at = Column(DateTime, nullable=True)
