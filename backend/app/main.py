import os
import sys
from pathlib import Path

# Đảm bảo thư mục backend luôn nằm trong sys.path dù chạy từ thư mục gốc hay thư mục con
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("smart_parking.main")

from app.config import settings
from app.services import mqtt_service, setup_iot_listeners, command_service, device_service
from app.routers import (
    auth_router,
    users_router,
    cards_router,
    slots_router,
    sessions_router,
    alerts_router,
    audits_router,
    devices_router,
    commands_router,
    alpr_router,
    tariffs_router,
    telemetry_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Quản lý vòng đời ứng dụng FastAPI:
    - Startup: Đăng ký toàn bộ IoT Message Listeners, khởi động MQTT Client Service,
      và chạy background task dọn dẹp các lệnh điều khiển quá hạn (Timeout Sweeper).
    - Shutdown: Ngắt kết nối và giải phóng tài nguyên an toàn.
    """
    # Kiểm tra tính toàn vẹn cấu hình MQTT bảo mật khi khởi động (Fail-Fast)
    if settings.MQTT_USERNAME and (settings.MQTT_PASSWORD is None or not str(settings.MQTT_PASSWORD).strip()) and not settings.TESTING:
        raise RuntimeError(
            f"[CRITICAL CONFIG ERROR] Cấu hình thiếu MQTT_PASSWORD cho tài khoản '{settings.MQTT_USERNAME}'. "
            f"Do Mosquitto Broker đã bật xác thực (allow_anonymous false), vui lòng thiết lập biến môi trường "
            f"MQTT_PASSWORD trong file .env trước khi khởi động backend."
        )

    setup_iot_listeners()
    mqtt_service.start()

    async def _system_sweeper():
        while True:
            try:
                await asyncio.sleep(15)
                await asyncio.to_thread(command_service.expire_stale_commands, timeout_seconds=30)
                await asyncio.to_thread(device_service.expire_stale_devices, timeout_seconds=settings.HEARTBEAT_TIMEOUT_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[System Sweeper] Lỗi tác vụ nền dọn dẹp lệnh/thiết bị quá hạn: {e}", exc_info=True)

    sweeper_task = asyncio.create_task(_system_sweeper())
    yield
    sweeper_task.cancel()
    try:
        await sweeper_task
    except asyncio.CancelledError:
        pass
    mqtt_service.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
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
app.include_router(commands_router, prefix="/api/v1")
app.include_router(alpr_router, prefix="/api/v1")
app.include_router(tariffs_router, prefix="/api/v1")
app.include_router(telemetry_router, prefix="/api/v1")


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
    summary="Health check endpoint (Liveness & Readiness)",
)
def health_check():
    """Health check dành cho monitoring và container orchestrator (Liveness & Readiness)."""
    from app.database import engine
    from sqlalchemy import text

    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        logger.warning(f"[Health Check] Lỗi kết nối CSDL: {e}")

    mqtt_ok = mqtt_service.is_connected

    return {
        "status": "healthy" if db_ok else "unhealthy",
        "liveness": db_ok,
        "mqtt_connected": mqtt_ok,
        "mqtt_status": "connected" if mqtt_ok else "disconnected",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        app_dir=str(BACKEND_DIR),
    )

