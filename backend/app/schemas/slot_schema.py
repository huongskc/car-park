from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import SlotStatus


class SlotStatusUpdate(BaseModel):
    """Schema cập nhật trạng thái ô đỗ (bảo trì, đặt trước, v.v.)."""
    status: SlotStatus = Field(..., description="Trạng thái mới của ô đỗ (EMPTY, OCCUPIED, RESERVED, MAINTENANCE)")


class SlotOut(BaseModel):
    """Schema trả về thông tin chi tiết ô đỗ xe."""
    id: str
    name: str
    site_id: str
    device_id: Optional[str] = None
    status: SlotStatus
    last_distance_cm: Optional[float] = None
    last_updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "SlotStatusUpdate",
    "SlotOut",
]
