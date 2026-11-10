"""Operator/admin controls on the disposable MySQL database."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.main import app
from app.services.mqtt_service import mqtt_service
from app.database import SessionLocal
from app.models.device import Device


def login(client, name, password):
    response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_password_change_tariff_and_payment_permissions(monkeypatch):
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=0)))
    client = TestClient(app)
    op = login(client, "operator", "op123")
    viewer = login(client, "viewer", "view123")
    admin = login(client, "admin", "admin123")

    assert client.post("/api/v1/auth/me/change-password", headers=op,
                       json={"current_password": "bad", "new_password": "newpass123"}).status_code == 400
    assert client.post("/api/v1/auth/me/change-password", headers=op,
                       json={"current_password": "op123", "new_password": "newpass123"}).status_code == 204
    assert client.post("/api/v1/auth/login",
                       json={"username": "operator", "password": "op123"}).status_code == 401
    op = login(client, "operator", "newpass123")

    tariff = {"name": "Phí demo mới", "flat_rate": 25000,
              "effective_from": datetime.now().isoformat()}
    assert client.post("/api/v1/tariffs", headers=op, json=tariff).status_code == 403
    assert client.post("/api/v1/tariffs", headers=admin, json=tariff).status_code == 201
    assert client.get("/api/v1/payments", headers=viewer).status_code == 403
    assert client.get("/api/v1/payments", headers=op).status_code == 200


def test_manual_gate_commands_need_reason_and_viewer_cannot_send(monkeypatch):
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=0)))
    with SessionLocal() as db:
        device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
        device.is_online = True
        device.last_heartbeat_at = datetime.now()
        db.commit()
    client = TestClient(app)
    op = login(client, "operator", "op123")
    viewer = login(client, "viewer", "view123")
    for action in ("HOLD_OPEN", "RESUME_AUTO"):
        body = {"gate_id": "GATE_IN", "action": action, "reason": "   "}
        assert client.post("/api/v1/devices/ESP32_GATE_01/commands",
                           headers=op, json=body).status_code == 400
        body["reason"] = "Kiểm tra mô hình"
        assert client.post("/api/v1/devices/ESP32_GATE_01/commands",
                           headers=viewer, json=body).status_code == 403
        assert client.post("/api/v1/devices/ESP32_GATE_01/commands",
                           headers=op, json=body).status_code == 201
