from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import AlertType, AlertSeverity, AlertStatus


class AlertResolve(BaseModel):
    """Schema cập nhật xử lý cảnh báo sự cố."""
    status: AlertStatus = Field(default=AlertStatus.RESOLVED, description="Trạng thái sau xử lý (RESOLVED hoặc DISMISSED)")


class AlertOut(BaseModel):
    """Schema trả về thông tin cảnh báo sự cố hệ thống."""
    id: int
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    device_id: Optional[str] = None
    session_id: Optional[int] = None
    status: AlertStatus
    resolved_by: Optional[int] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "AlertResolve",
    "AlertOut",
]
