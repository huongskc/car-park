from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    """Schema trả về nhật ký kiểm toán hệ thống."""
    id: int
    user_id: Optional[int] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = ["AuditLogOut"]
