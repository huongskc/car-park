from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import GateId, CommandAction, CommandStatus


class CommandSend(BaseModel):
    """Schema gửi lệnh điều khiển phần cứng Barrier và bo mạch ESP32."""
    gate_id: GateId = Field(..., description="Cổng kiểm soát mục tiêu (GATE_IN hoặc GATE_OUT)")
    action: CommandAction = Field(..., description="Hành động điều khiển (OPEN, CLOSE, REBOOT, UPDATE_CONFIG)")
    reason: Optional[str] = Field(None, max_length=255, description="Lý do thực hiện lệnh (ví dụ: xe ưu tiên, quẹt thẻ lỗi)")


class CommandAckPayload(BaseModel):
    """Schema bản tin ACK phản hồi từ vi điều khiển ESP32 gửi qua MQTT."""
    command_id: str = Field(..., description="Mã định danh lệnh gốc")
    status: str = Field(default="SUCCESS", description="Trạng thái thực thi phần cứng (SUCCESS, FAILED, BLOCKED)")
    reason: Optional[str] = Field(None, description="Chi tiết kết quả thực thi cơ khí")


class CommandOut(BaseModel):
    """Schema trả về thông tin chi tiết lệnh điều khiển."""
    id: str
    device_id: str
    gate_id: GateId
    action: CommandAction
    status: CommandStatus
    sent_by_user_id: Optional[int] = None
    ack_status: Optional[str] = None
    ack_reason: Optional[str] = None
    sent_at: datetime
    acked_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CommandSend",
    "CommandAckPayload",
    "CommandOut",
]
