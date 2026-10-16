from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.enums import PaymentMethod, PaymentStatus


class PaymentOut(BaseModel):
    """Schema trả về thông tin giao dịch thu phí gửi xe."""
    id: int
    session_id: int
    amount: float
    payment_method: PaymentMethod
    payment_status: PaymentStatus
    transaction_code: Optional[str] = None
    paid_at: datetime
    operator_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


__all__ = ["PaymentOut"]
