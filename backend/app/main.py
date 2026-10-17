import os
import sys
from pathlib import Path

# Đảm bảo thư mục backend luôn nằm trong sys.path dù chạy từ thư mục gốc hay thư mục con
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    auth_router,
    users_router,
    cards_router,
    slots_router,
    sessions_router,
    alerts_router,
    audits_router,
    devices_router,
)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ----------------------------------------------------
# Cấu hình CORS Middleware hỗ trợ Web Dashboard Frontend
# ----------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------
# Tích hợp Router Nghiệp vụ (Prefix: /api/v1)
# ----------------------------------------------------
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(cards_router, prefix="/api/v1")
app.include_router(slots_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(alerts_router, prefix="/api/v1")
app.include_router(audits_router, prefix="/api/v1")
app.include_router(devices_router, prefix="/api/v1")


@app.get(
    "/",
    tags=["Root & Health"],
    summary="Kiểm tra trạng thái hệ thống",
)
def root_endpoint():
    """Endpoint gốc trả về thông tin trạng thái và phiên bản backend."""
    return {
        "system": settings.APP_NAME,
        "version": "1.0.0",
        "status": "online",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
    }


@app.get(
    "/health",
    tags=["Root & Health"],
    summary="Health check endpoint",
)
def health_check():
    """Health check dành cho monitoring và container orchestrator."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        app_dir=str(BACKEND_DIR),
    )

