from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric, Enum, func
from app.models.base import Base
from app.models.enums import TariffFeeType


class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    fee_type = Column(Enum(TariffFeeType), nullable=False, default=TariffFeeType.FLAT)
    flat_rate = Column(Numeric(10, 2), nullable=False, default=20000.00)
    hourly_rate = Column(Numeric(10, 2), nullable=False, default=0.00)
    grace_period_mins = Column(Integer, nullable=False, default=5)
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime, nullable=False, server_default=func.now())
