from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import SessionStatus, PaymentMethod


class SessionCreate(BaseModel):
    """Schema tạo phiên gửi xe mới (sử dụng khi xe vào hoặc test)."""
    card_uid: str = Field(..., min_length=4, max_length=50, description="Mã UID thẻ RFID quẹt tại barrier vào", examples=["A1B2C3D4"])
    slot_id: Optional[str] = Field(None, max_length=20, description="Mã ô đỗ được gán (nếu có)", examples=["SLOT_A1"])
    entry_plate: Optional[str] = Field(None, max_length=20, description="Biển số xe nhận diện từ camera ALPR", examples=["30A-999.88"])
    entry_image_url: Optional[str] = Field(None, max_length=255, description="Đường dẫn ảnh chụp lúc xe vào", examples=["/images/in_20261016_01.jpg"])


class SessionResolvePlate(BaseModel):
    """Schema duyệt ngoại lệ lệch biển số xe lúc ra."""
    approved: bool = Field(..., description="Quyết định duyệt: True cho phép xe ra, False từ chối")
    note: str = Field(..., min_length=1, max_length=255, description="Ghi chú / Lý do quyết định của nhân viên", examples=["Đã kiểm tra vé và đối chiếu căn cước công dân"])


class SessionCheckout(BaseModel):
    """Schema thanh toán phí gửi xe và kết thúc phiên."""
    payment_method: PaymentMethod = Field(default=PaymentMethod.CASH, description="Phương thức thanh toán (CASH, SIMULATED_MOMO)")


class SessionOut(BaseModel):
    """Schema trả về thông tin chi tiết phiên gửi xe."""
    id: int
    session_code: str
    card_uid: str
    slot_id: Optional[str] = None
    tariff_id: int
    status: SessionStatus
    entry_time: datetime
    entry_plate: Optional[str] = None
    entry_image_url: Optional[str] = None
    exit_time: Optional[datetime] = None
    exit_plate: Optional[str] = None
    exit_image_url: Optional[str] = None
    fee_amount: float
    is_paid: bool
    review_reason: Optional[str] = None
    reviewed_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "SessionCreate",
    "SessionResolvePlate",
    "SessionCheckout",
    "SessionOut",
]
