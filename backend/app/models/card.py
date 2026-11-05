from sqlalchemy import Column, String, Boolean, Enum, Date
from app.models.base import Base, TimestampMixin
from app.models.enums import CardType


class Card(Base, TimestampMixin):
    __tablename__ = "cards"

    uid = Column(String(50), primary_key=True)
    card_type = Column(Enum(CardType), nullable=False, default=CardType.VISITOR, index=True)
    owner_name = Column(String(100), nullable=True)
    registered_plate = Column(String(20), nullable=True, index=True)
    valid_from = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
