import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.core.dependencies import require_role
from app.core.security import decode_access_token
from app.database import SessionLocal
from app.models.enums import UserRole
from app.models.user import User
from app.services.websocket_manager import websocket_manager

logger = logging.getLogger("smart_parking.websocket_router")

router = APIRouter(tags=["WebSockets"])


@router.websocket("/ws")
@router.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Kênh giao tiếp thời gian thực WebSockets dành cho Web Dashboard:
    - Đẩy các sự kiện tức thì: đổi trạng thái ô đỗ, xe vào/ra, đổi trạng thái barrier, cảnh báo an ninh.
    - Hỗ trợ client gửi chuỗi 'ping' để nhận 'pong' kiểm tra đường truyền.
    """
    token = websocket.query_params.get("token")
    claims = decode_access_token(token) if token else None
    if not claims or not claims.get("sub"):
        await websocket.close(code=1008)
        return
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=claims["sub"]).first()
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await websocket_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"[WebSocket Router] Kết nối gặp sự cố hoặc kết thúc đột ngột: {e}")
        websocket_manager.disconnect(websocket)


@router.get("/api/v1/ws/stats", summary="Thống kê kết nối WebSocket hiện tại")
def websocket_stats(_user: User = Depends(require_role(UserRole.VIEWER))):
    """Trả về số lượng client WebSockets đang duy trì kết nối."""
    return {
        "active_connections": len(websocket_manager.active_connections),
        "status": "online",
    }
