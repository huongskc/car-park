"""Tests for Realtime WebSocket Manager and Endpoints (Milestone 5.2)."""
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services.websocket_manager import websocket_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_websocket_stats_endpoint(client):
    """Kiểm tra REST API tra cứu trạng thái kết nối WebSocket."""
    response = client.get("/api/v1/ws/stats")
    assert response.status_code == 200
    data = response.json()
    assert "active_connections" in data
    assert data["status"] == "online"


def test_websocket_connection_and_ping_pong(client):
    """Kiểm tra kết nối WebSocket thực tế: Gửi ping nhận pong."""
    with client.websocket_connect("/ws") as ws:
        # Gửi ping
        ws.send_text("ping")
        response = ws.receive_text()
        assert response == "pong"


def test_websocket_broadcast_sync(client):
    """Kiểm tra broadcast sự kiện qua WebSocket tới client đang lắng nghe."""
    with client.websocket_connect("/ws") as ws:
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
