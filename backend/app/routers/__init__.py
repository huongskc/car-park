from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.cards import router as cards_router
from app.routers.slots import router as slots_router
from app.routers.sessions import router as sessions_router
from app.routers.alerts import router as alerts_router
from app.routers.audits import router as audits_router
from app.routers.devices import router as devices_router
from app.routers.commands import router as commands_router
from app.routers.alpr import router as alpr_router
from app.routers.tariffs import router as tariffs_router
from app.routers.telemetry import router as telemetry_router

__all__ = [
    "auth_router",
    "users_router",
    "cards_router",
    "slots_router",
    "sessions_router",
    "alerts_router",
    "audits_router",
    "devices_router",
    "commands_router",
    "alpr_router",
    "tariffs_router",
    "telemetry_router",
]
