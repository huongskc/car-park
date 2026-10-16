from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.cards import router as cards_router
from app.routers.slots import router as slots_router

__all__ = [
    "auth_router",
    "users_router",
    "cards_router",
    "slots_router",
]
