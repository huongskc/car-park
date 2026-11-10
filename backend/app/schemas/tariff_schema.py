from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import TariffFeeType


class TariffOut(BaseModel):
    """Schema trả về thông tin biểu phí tính tiền gửi xe."""
    id: int
    name: str = Field(..., description="Tên biểu phí")
    fee_type: TariffFeeType = Field(..., description="Hình thức tính phí (FLAT: theo lượt, HOURLY: theo giờ)")
    flat_rate: float = Field(..., description="Mức phí cố định theo lượt")
    hourly_rate: float = Field(..., description="Mức phí tính theo giờ")
    grace_period_mins: int = Field(..., description="Thời gian ân hạn miễn phí (phút)")
    is_active: bool = Field(..., description="Trạng thái hiệu lực của biểu phí")
    effective_from: datetime = Field(..., description="Thời điểm bắt đầu áp dụng biểu phí")

    model_config = ConfigDict(from_attributes=True)


class FlatTariffCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    flat_rate: Decimal = Field(..., ge=0, le=99999999.99, decimal_places=2)
    effective_from: datetime


__all__ = [
    "TariffOut",
]
