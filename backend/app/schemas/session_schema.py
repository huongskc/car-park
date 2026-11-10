from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import SessionStatus, PaymentMethod


class SessionCreate(BaseModel):
    """Schema tạo phiên gửi xe mới (sử dụng khi xe vào hoặc test)."""
    card_uid: str = Field(..., min_length=4, max_length=50, description="Mã UID thẻ RFID quẹt tại barrier vào", examples=["A1B2C3D4"])
    device_id: str = Field(..., min_length=3, max_length=50)
    entry_plate: Optional[str] = Field(None, max_length=20)
    entry_image_url: Optional[str] = Field(None, max_length=255, description="Đường dẫn ảnh chụp lúc xe vào", examples=["/images/in_20261016_01.jpg"])


class EntryPlateConfirm(BaseModel):
    plate: str = Field(..., min_length=4, max_length=20)
    note: str = Field(..., min_length=1, max_length=255)
    image_url: Optional[str] = Field(None, max_length=255)


class ExitPrepare(BaseModel):
    exit_plate: Optional[str] = Field(None, max_length=20)
    exit_image_url: Optional[str] = Field(None, max_length=255)


class EntryCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)
    confirmed_departure: bool = False


class PassageConfirm(BaseModel):
    confirmed_passage: bool = Field(..., description="Nhân viên xác nhận đã thấy xe qua cổng")
    note: Optional[str] = Field(None, max_length=255)


class ExitCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)
    confirmed_not_exited: bool = Field(...)


class SessionResolvePlate(BaseModel):
    """Schema duyệt ngoại lệ lệch biển số xe lúc ra."""
    approved: bool = Field(..., description="Quyết định duyệt: True cho phép xe ra, False từ chối")
    note: str = Field(..., min_length=1, max_length=255, description="Ghi chú / Lý do quyết định của nhân viên", examples=["Đã kiểm tra vé và đối chiếu căn cước công dân"])
    actual_exit_plate: Optional[str] = Field(None, max_length=20, description="Biển số xe thực tế lúc ra nếu cần đính chính (khi camera OCR đọc sai)", examples=["30A-999.88"])


class SessionCancel(BaseModel):
    """Schema hủy phiên gửi xe ngoại lệ dành riêng cho Quản trị viên (ADMIN)."""
    reason: str = Field(..., min_length=3, max_length=255, description="Lý do quản trị viên hủy phiên gửi xe", examples=["Xe đã được giải phóng thủ công ngoài thực địa"])
    confirmed_exit: bool = Field(..., description="Xác nhận phương tiện đã thực tế rời khỏi bãi đỗ")


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
    device_id: Optional[str] = None
    entry_time: Optional[datetime] = None
    entry_plate: Optional[str] = None
    entry_ocr_plate: Optional[str] = None
    entry_image_url: Optional[str] = None
    exit_time: Optional[datetime] = None
    exit_plate: Optional[str] = None
    exit_ocr_plate: Optional[str] = None
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
    "SessionCancel",
    "SessionCheckout",
    "SessionOut",
]
