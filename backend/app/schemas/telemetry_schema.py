from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class TelemetryLogOut(BaseModel):
    """Schema trả về bản ghi nhật ký đo lường cảm biến siêu âm ô đỗ."""
    id: int
    device_id: str = Field(..., description="Mã thiết bị đo lường")
    slot_id: str = Field(..., description="Mã ô đỗ")
    distance_cm: float = Field(..., description="Khoảng cách đo được (cm)")
    is_occupied: bool = Field(..., description="Trạng thái có xe đỗ dựa trên ngưỡng đo")
    boot_id: Optional[str] = Field(None, description="Mã phiên khởi động của ESP32")
    seq: Optional[int] = Field(None, description="Số thứ tự gói tin telemetry")
    sampled_at: datetime = Field(..., description="Thời điểm cảm biến lấy mẫu trên thiết bị")
    received_at: datetime = Field(..., description="Thời điểm máy chủ tiếp nhận bản tin")

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "TelemetryLogOut",
]
