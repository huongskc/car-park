from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.telemetry import TelemetryLog
from app.models.slot import Slot
from app.models.device import Device
from app.models.enums import DeviceStatus


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_tokens(client):
    res_vi = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
    assert res_vi.status_code == 200
    vi_token = res_vi.json()["access_token"]
    return {
        "viewer": {"Authorization": f"Bearer {vi_token}"},
    }


def test_tariffs_unauthenticated(client):
    res = client.get("/api/v1/tariffs")
    assert res.status_code == 401


def test_tariffs_list_and_detail(client, auth_tokens):
    headers = auth_tokens["viewer"]

    # 1. Tra cứu danh sách biểu phí
    res = client.get("/api/v1/tariffs", headers=headers)
    assert res.status_code == 200
    tariffs = res.json()
    assert isinstance(tariffs, list)
    assert len(tariffs) >= 1

    first_tariff = tariffs[0]
    assert "id" in first_tariff
    assert "name" in first_tariff
    assert "fee_type" in first_tariff
    assert "flat_rate" in first_tariff
    assert "hourly_rate" in first_tariff
    assert "grace_period_mins" in first_tariff
    assert "is_active" in first_tariff
    assert "effective_from" in first_tariff

    # 2. Lọc theo is_active
    res_active = client.get("/api/v1/tariffs?is_active=true", headers=headers)
    assert res_active.status_code == 200
    for t in res_active.json():
        assert t["is_active"] is True

    # 3. Tra cứu chi tiết một biểu phí hợp lệ
    tariff_id = first_tariff["id"]
    res_detail = client.get(f"/api/v1/tariffs/{tariff_id}", headers=headers)
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == tariff_id

    # 4. Tra cứu biểu phí không tồn tại -> 404
    res_404 = client.get("/api/v1/tariffs/999999", headers=headers)
    assert res_404.status_code == 404


def test_slot_telemetry(client, auth_tokens):
    headers = auth_tokens["viewer"]

    # 1. Unauthenticated -> 401
    res_unauth = client.get("/api/v1/slots/SLOT_A1/telemetry")
    assert res_unauth.status_code == 401

    # 2. Slot không tồn tại -> 404
    res_404 = client.get("/api/v1/slots/NON_EXISTENT_SLOT/telemetry", headers=headers)
    assert res_404.status_code == 404

    # 3. Chèn dữ liệu đo lường thử nghiệm cho SLOT_A1
    db = SessionLocal()
    now = datetime.now()
    t1 = now - timedelta(minutes=25)
    t2 = now - timedelta(minutes=15)
    t3 = now - timedelta(minutes=5)

    entries = [
        TelemetryLog(
            device_id="ESP32_GATE_01",
            slot_id="SLOT_A1",
            distance_cm=45.50,
            is_occupied=False,
            sampled_at=t1,
        ),
        TelemetryLog(
            device_id="ESP32_GATE_01",
            slot_id="SLOT_A1",
            distance_cm=20.00,
            is_occupied=True,
            sampled_at=t2,
        ),
        TelemetryLog(
            device_id="ESP32_GATE_01",
            slot_id="SLOT_A1",
            distance_cm=18.50,
            is_occupied=True,
            sampled_at=t3,
        ),
    ]
    db.add_all(entries)
    db.commit()

    try:
        # Lấy tối đa 2 bản ghi mới nhất -> phải trả về t2 và t3, xếp theo thứ tự thời gian tăng dần [t2, t3]
        res = client.get("/api/v1/slots/SLOT_A1/telemetry?limit=2", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        # Kiểm tra thứ tự tăng dần theo thời gian
        assert data[0]["distance_cm"] == 20.00
        assert data[1]["distance_cm"] == 18.50

        # Lọc trong vòng 10 phút gần nhất -> chỉ có t3
        res_filtered = client.get("/api/v1/slots/SLOT_A1/telemetry?minutes=10", headers=headers)
        assert res_filtered.status_code == 200
        filtered_data = res_filtered.json()
        assert len(filtered_data) == 1
        assert filtered_data[0]["distance_cm"] == 18.50

    finally:
        db.query(TelemetryLog).filter(TelemetryLog.slot_id == "SLOT_A1").delete()
        db.commit()
        db.close()


def test_telemetry_history(client, auth_tokens):
    headers = auth_tokens["viewer"]

    # 1. Unauthenticated -> 401
    res_unauth = client.get("/api/v1/telemetry/history")
    assert res_unauth.status_code == 401

    # 2. Chèn dữ liệu mẫu
    db = SessionLocal()
    dev2 = db.query(Device).filter(Device.id == "ESP32_GATE_02").first()
    if not dev2:
        dev2 = Device(
            id="ESP32_GATE_02",
            name="Node 2",
            site_id="PTIT_ZONE_1",
            auth_token="token_esp32_secret_ptit_2026_02",
            status=DeviceStatus.ACTIVATED,
        )
        db.add(dev2)
        db.commit()

    now = datetime.now()
    t1 = now - timedelta(minutes=10)
    t2 = now - timedelta(minutes=2)

    entry1 = TelemetryLog(
        device_id="ESP32_GATE_01",
        slot_id="SLOT_A1",
        distance_cm=50.00,
        is_occupied=False,
        sampled_at=t1,
    )
    entry2 = TelemetryLog(
        device_id="ESP32_GATE_02",
        slot_id="SLOT_B1",
        distance_cm=12.00,
        is_occupied=True,
        sampled_at=t2,
    )
    db.add_all([entry1, entry2])
    db.commit()

    try:
        # Lọc theo slot_id
        res_slot = client.get("/api/v1/telemetry/history?slot_id=SLOT_A1", headers=headers)
        assert res_slot.status_code == 200
        data_slot = res_slot.json()
        assert len(data_slot) == 1
        assert data_slot[0]["slot_id"] == "SLOT_A1"

        # Lọc theo device_id
        res_dev = client.get("/api/v1/telemetry/history?device_id=ESP32_GATE_02", headers=headers)
        assert res_dev.status_code == 200
        data_dev = res_dev.json()
        assert len(data_dev) == 1
        assert data_dev[0]["device_id"] == "ESP32_GATE_02"
        assert data_dev[0]["slot_id"] == "SLOT_B1"

    finally:
        db.query(TelemetryLog).filter(TelemetryLog.slot_id.in_(["SLOT_A1", "SLOT_B1"])).delete()
        db.query(Device).filter(Device.id == "ESP32_GATE_02").delete()
        db.commit()
        db.close()
