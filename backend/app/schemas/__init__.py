from app.schemas.auth_schema import (
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.user_schema import (
    UserCreate,
    UserStatusUpdate,
)
from app.schemas.card_schema import (
    CardCreate,
    CardStatusUpdate,
    CardOut,
)
from app.schemas.slot_schema import (
    SlotStatusUpdate,
    SlotOut,
)

__all__ = [
    # Auth & User
    "LoginRequest",
    "TokenResponse",
    "UserOut",
    "UserCreate",
    "UserStatusUpdate",
    # Cards
    "CardCreate",
    "CardStatusUpdate",
    "CardOut",
    # Slots
    "SlotStatusUpdate",
    "SlotOut",
]
