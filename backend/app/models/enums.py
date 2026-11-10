from enum import Enum


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class DeviceStatus(str, Enum):
    REGISTERED = "REGISTERED"
    ACTIVATED = "ACTIVATED"
    MAINTENANCE = "MAINTENANCE"
    SUSPENDED = "SUSPENDED"
    DECOMMISSIONED = "DECOMMISSIONED"


class SlotStatus(str, Enum):
    EMPTY = "EMPTY"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    MAINTENANCE = "MAINTENANCE"
    UNKNOWN = "UNKNOWN"


class CardType(str, Enum):
    VISITOR = "VISITOR"
    MONTHLY = "MONTHLY"


class TariffFeeType(str, Enum):
    FLAT = "FLAT"
    HOURLY = "HOURLY"


class SessionStatus(str, Enum):
    ENTRY_PENDING = "ENTRY_PENDING"
    ENTRY_ACKED_WAITING_PASS = "ENTRY_ACKED_WAITING_PASS"
    ACTIVE = "ACTIVE"
    EXIT_READY = "EXIT_READY"
    PAID_WAITING_EXIT = "PAID_WAITING_EXIT"
    EXIT_ACKED_WAITING_PASS = "EXIT_ACKED_WAITING_PASS"
    COMPLETED = "COMPLETED"
    PENDING_REVIEW = "PENDING_REVIEW"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    CASH = "CASH"
    SIMULATED_MOMO = "SIMULATED_MOMO"
    SYSTEM = "SYSTEM"


class PaymentStatus(str, Enum):
    PAID = "PAID"
    PENDING = "PENDING"
    REFUNDED = "REFUNDED"


class GateId(str, Enum):
    GATE_IN = "GATE_IN"
    GATE_OUT = "GATE_OUT"


class CommandAction(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    HOLD_OPEN = "HOLD_OPEN"
    RESUME_AUTO = "RESUME_AUTO"
    REBOOT = "REBOOT"
    UPDATE_CONFIG = "UPDATE_CONFIG"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    ACKED = "ACKED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class AlertType(str, Enum):
    PLATE_MISMATCH = "PLATE_MISMATCH"
    UNAUTHORIZED_CARD = "UNAUTHORIZED_CARD"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    LONG_OCCUPANCY = "LONG_OCCUPANCY"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"
