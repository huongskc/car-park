import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger("smart_parking.websocket_manager")


class WebSocketManager:
    """
    Quản lý kết nối WebSockets thời gian thực phục vụ Web Dashboard và các ứng dụng giám sát.
    Hỗ trợ phát sự kiện (broadcast) từ cả luồng bất đồng bộ (asyncio) và luồng đồng bộ (MQTT thread).
    """

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Lưu lại event loop chính của FastAPI để hỗ trợ broadcast từ các thread đồng bộ."""
        self._main_loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        """Chấp nhận kết nối WebSocket mới và đưa vào danh sách lắng nghe."""
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        logger.info(
            f"[WebSocket] Client mới kết nối (Tổng số kết nối đang hoạt động: {len(self.active_connections)})"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Gỡ bỏ kết nối khi client đóng tab hoặc ngắt kết nối."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(
                f"[WebSocket] Client ngắt kết nối (Còn lại: {len(self.active_connections)})"
            )

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Bắn sự kiện JSON thời gian thực tới toàn bộ các client đang kết nối.
        Cấu trúc bản tin:
        {
            "event": "SLOT_STATUS_CHANGED" | "CAR_ENTERED" | "CAR_EXITED" | ...,
            "data": { ... },
            "timestamp": "2026-11-05T15:30:00"
        }
        """
        if not self.active_connections:
            return

        message = {
            "event": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        text_payload = json.dumps(message, ensure_ascii=False, default=str)

        disconnected_clients: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text_payload)
            except Exception as e:
                logger.warning(f"[WebSocket] Lỗi gửi bản tin tới client: {e}")
                disconnected_clients.append(connection)

        for client in disconnected_clients:
            self.disconnect(client)

    def broadcast_sync(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Cầu nối phát sự kiện an toàn từ các luồng đồng bộ (ví dụ MQTT thread hoặc sync service)
        sang Event Loop chính của ứng dụng FastAPI.
        """
        if not self.active_connections:
            return

        loop = self._main_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.broadcast(event_type, data), loop)
            try:
                future.result(timeout=3.0)
            except Exception as e:
                logger.warning(f"[WebSocket Sync] Lỗi gửi bản tin qua loop: {e}")
        else:
            try:
                asyncio.run(self.broadcast(event_type, data))
            except Exception as e:
                logger.debug(f"[WebSocket Sync] Bỏ qua broadcast '{event_type}': {e}")


websocket_manager = WebSocketManager()
