from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import CardType


class CardCreate(BaseModel):
    """Schema đăng ký thẻ RFID mới vào hệ thống."""
    uid: str = Field(..., min_length=4, max_length=50, description="Mã UID vật lý của thẻ RFID", examples=["RFID_A1B2C3D4"])
    card_type: CardType = Field(default=CardType.VISITOR, description="Loại thẻ (MONTHLY, VISITOR, VIP, STAFF)")
    owner_name: Optional[str] = Field(None, max_length=100, description="Họ và tên chủ sở hữu thẻ", examples=["Nguyễn Văn An"])
    registered_plate: Optional[str] = Field(None, max_length=20, description="Biển số xe đăng ký gắn với thẻ (nếu có)", examples=["29A-888.88"])


class CardStatusUpdate(BaseModel):
    """Schema cập nhật trạng thái hoạt động (khóa / mở khóa) của thẻ RFID."""
    is_active: bool = Field(..., description="Trạng thái kích hoạt của thẻ (True: kích hoạt, False: khóa/báo mất)")


class CardOut(BaseModel):
    """Schema trả về thông tin thẻ RFID."""
    uid: str
    card_type: CardType
    owner_name: Optional[str] = None
    registered_plate: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CardCreate",
    "CardStatusUpdate",
    "CardOut",
]
