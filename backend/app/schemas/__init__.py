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
from app.schemas.session_schema import (
    SessionCreate,
    SessionResolvePlate,
    SessionCheckout,
    SessionOut,
)
from app.schemas.payment_schema import (
    PaymentOut,
)
from app.schemas.alert_schema import (
    AlertResolve,
    AlertOut,
)
from app.schemas.audit_schema import (
    AuditLogOut,
)
from app.schemas.device_schema import (
    DeviceRegister,
    DeviceConfigUpdate,
    DeviceStatusChange,
    DeviceConfigOut,
    DeviceOut,
)
from app.schemas.command_schema import (
    CommandSend,
    CommandAckPayload,
    CommandOut,
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
    # Sessions
    "SessionCreate",
    "SessionResolvePlate",
    "SessionCheckout",
    "SessionOut",
    # Payments
    "PaymentOut",
    # Alerts
    "AlertResolve",
    "AlertOut",
    # Audits
    "AuditLogOut",
    # Devices & IoT Lifecycle
    "DeviceRegister",
    "DeviceConfigUpdate",
    "DeviceStatusChange",
    "DeviceConfigOut",
    "DeviceOut",
    # Barrier Commands & ACK
    "CommandSend",
    "CommandAckPayload",
    "CommandOut",
]
