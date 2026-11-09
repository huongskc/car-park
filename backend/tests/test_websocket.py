"""Tests for Realtime WebSocket Manager and Endpoints (Milestone 5.2)."""
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.database import SessionLocal
from app.main import app
from app.models.user import User
from app.services.websocket_manager import websocket_manager


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def ws_path(client):
    login = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
    assert login.status_code == 200
    return "/ws?token=" + login.json()["access_token"]


def test_websocket_stats_endpoint(client, ws_path):
    """Kiểm tra REST API tra cứu trạng thái kết nối WebSocket."""
    assert client.get("/api/v1/ws/stats").status_code == 401
    response = client.get("/api/v1/ws/stats", headers={
        "Authorization": "Bearer " + ws_path.split("token=", 1)[1],
    })
    assert response.status_code == 200
    data = response.json()
    assert "active_connections" in data
    assert data["status"] == "online"


def test_websocket_connection_and_ping_pong(client, ws_path):
    """Kiểm tra kết nối WebSocket thực tế: Gửi ping nhận pong."""
    with client.websocket_connect(ws_path) as ws:
        # Gửi ping
        ws.send_text("ping")
        response = ws.receive_text()
        assert response == "pong"


def test_websocket_broadcast_sync(client, ws_path):
    """Kiểm tra broadcast sự kiện qua WebSocket tới client đang lắng nghe."""
    with client.websocket_connect(ws_path) as ws:
        # Client đang kết nối, broadcast sự kiện đồng bộ
        test_payload = {"slot_id": "SLOT_A01", "status": "OCCUPIED"}
        websocket_manager.broadcast_sync("SLOT_STATUS_CHANGED", test_payload)

        # Nhận tin nhắn JSON broadcast
        raw_msg = ws.receive_text()
        import json
        msg = json.loads(raw_msg)
        assert msg["event"] == "SLOT_STATUS_CHANGED"
        assert msg["data"]["slot_id"] == "SLOT_A01"
        assert msg["data"]["status"] == "OCCUPIED"
        assert "timestamp" in msg


def test_websocket_rejects_anonymous_invalid_and_disabled_users(client, ws_path):
    for path in ("/ws", "/api/v1/ws?token=invalid"):
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path):
                pass
        assert error.value.code == 1008

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username="viewer").one()
        user.is_active = False
        db.commit()
    finally:
        db.close()
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(ws_path):
            pass
    assert error.value.code == 1008
