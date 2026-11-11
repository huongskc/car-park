"""Workflow tests on the disposable database managed by conftest.py."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.card import Card
from app.models.alert import Alert
from app.models.tariff import Tariff
from app.models.command import Command
from app.models.device import Device
from app.models.enums import (
    AlertType, CommandStatus, DeviceStatus, GateId, PaymentMethod, SessionStatus, SlotStatus,
)
from app.models.payment import Payment
from app.models.session import ParkingSession
from app.models.slot import Slot
from app.models.telemetry import TelemetryLog
from app.services.command_service import command_service
from app.services.alpr_service import alpr_service
from app.services.iot_listener import iot_listener
from app.services.mqtt_service import mqtt_service
from app.services.parking_engine import parking_engine
from app.services.websocket_manager import websocket_manager
from app.services.parking_flow import (
    approve_exit, cancel_entry, checkout, confirm_entry_plate, create_entry,
    prepare_exit, retry_gate, confirm_passage, cancel_exit, record_entry_ocr,
)
from app.schemas.alpr_schema import CaptureResult


@pytest.fixture
def db(monkeypatch):
    session = SessionLocal()
    device = session.query(Device).filter_by(id="ESP32_GATE_01").one()
    device.is_online = True
    device.last_heartbeat_at = datetime.now()
    session.commit()
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=0)))
    yield session
    session.close()


def ack(db, session_id, gate, success=True, confirm=True):
    command = (db.query(Command).filter_by(session_id=session_id, gate_id=gate,
                                           status=CommandStatus.SENT)
               .order_by(Command.sent_at.desc(), Command.id.desc()).first())
    assert command is not None
    assert command_service.process_ack(
        {"commandId": command.id, "status": "SUCCESS" if success else "FAILED"},
        db, topic_device_id="ESP32_GATE_01")
    db.expire_all()
    if success and confirm:
        confirm_passage(db, session_id, gate, SimpleNamespace(id=2))
        db.expire_all()
    return command


def sample_twice(topic, payload):
    iot_listener.handle_telemetry(topic, payload)
    iot_listener.handle_telemetry(topic, payload)


def test_visitor_full_flow(db, monkeypatch):
    events = []
    monkeypatch.setattr(websocket_manager, "broadcast_sync",
                        lambda event, data: events.append((event, data)))
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A-123.45")
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert obj.entry_time is None
    assert obj.slot_id == "SLOT_A1"
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED
    ack(db, obj.id, GateId.GATE_IN)
    assert obj.status == SessionStatus.ACTIVE
    assert obj.entry_time is not None
    assert any(event == "CAR_ENTERED" and data["session_id"] == obj.id
               for event, data in events)
    prepare_exit(db, obj.id, "30A12345")
    assert obj.status == SessionStatus.EXIT_READY
    user = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    checkout(db, obj.id, PaymentMethod.CASH, user)
    assert obj.status == SessionStatus.PAID_WAITING_EXIT
    assert obj.exit_time is None
    ack(db, obj.id, GateId.GATE_OUT)
    assert obj.status == SessionStatus.COMPLETED
    assert obj.exit_time is not None
    assert any(event == "CAR_EXITED" and data["session_id"] == obj.id
               for event, data in events)
    assert any(event == "BARRIER_STATUS" and data["status"] == "ACKED"
               for event, data in events)
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 1
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED


def test_monthly_auto_exit_and_expired_fee(db):
    monthly = create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A-999.88")
    assert float(monthly.fee_amount) == 0
    ack(db, monthly.id, GateId.GATE_IN)
    prepare_exit(db, monthly.id, "30A99988")
    assert monthly.status == SessionStatus.PAID_WAITING_EXIT
    ack(db, monthly.id, GateId.GATE_OUT)
    assert monthly.status == SessionStatus.COMPLETED
    card = db.query(Card).filter_by(uid="E5F6G7H8").one()
    card.valid_until = date.today() - timedelta(days=1)
    db.query(Slot).filter_by(id=monthly.slot_id).one().status = SlotStatus.EMPTY
    db.commit()
    expired = create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A-999.88")
    assert float(expired.fee_amount) == 20000


def test_missing_or_wrong_entry_plate_waits_for_operator(db):
    obj = create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "29B-000.01")
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert db.query(Command).filter_by(session_id=obj.id).count() == 0
    with pytest.raises(HTTPException):
        retry_gate(db, obj.id)
    with pytest.raises(HTTPException):
        confirm_entry_plate(db, obj.id, "29B-000.01")
    confirm_entry_plate(db, obj.id, "30A-999.88", note="Corrected OCR")
    assert db.query(Command).filter_by(session_id=obj.id).count() == 1


def test_missing_plate_and_sensor_release_only_after_exit(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert db.query(Command).filter_by(session_id=obj.id).count() == 0
    confirm_entry_plate(db, obj.id, "30A12345", note="Checked demo image")
    ack(db, obj.id, GateId.GATE_IN)
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry"
    sample_twice(topic, {"token": device.auth_token,
                         "slotId": obj.slot_id, "distanceCm": 8})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.OCCUPIED
    prepare_exit(db, obj.id, "30A12345")
    user = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    checkout(db, obj.id, PaymentMethod.CASH, user)
    ack(db, obj.id, GateId.GATE_OUT)
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.OCCUPIED
    sample_twice(topic, {"token": device.auth_token,
                         "slotId": obj.slot_id, "distanceCm": 50})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.EMPTY


def test_reserved_last_slot_blocks_next_entry(db):
    create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    with pytest.raises(HTTPException) as error:
        create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A99988")
    assert error.value.status_code == 409


def test_open_session_blocks_slot_even_if_status_is_incorrectly_empty(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    slot = db.query(Slot).filter_by(id=obj.slot_id).one()
    slot.status = SlotStatus.EMPTY
    db.commit()
    with pytest.raises(HTTPException) as error:
        create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A99988")
    assert error.value.status_code == 409


def test_failed_ack_can_retry_and_pending_entry_can_cancel(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN, success=False)
    assert obj.status == SessionStatus.ENTRY_PENDING and obj.entry_time is None
    retry_gate(db, obj.id)
    ack(db, obj.id, GateId.GATE_IN)
    assert obj.status == SessionStatus.ACTIVE


def test_cancel_pending_entry_returns_reserved_slot(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    user = SimpleNamespace(id=2)
    with pytest.raises(HTTPException) as error:
        cancel_entry(db, obj.id, "   ", user)
    assert error.value.status_code == 400
    cancel_entry(db, obj.id, "Car turned away", user)
    assert obj.status == SessionStatus.CANCELLED
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.EMPTY


def test_manual_slot_update_requires_meaningful_reason(db):
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    result = client.patch("/api/v1/slots/SLOT_A1/status", headers=headers,
                          json={"status": "EMPTY", "reason": "   "})
    assert result.status_code == 400


def test_publish_failure_and_retry_do_not_charge_twice(db, monkeypatch):
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=4)))
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert db.query(Command).filter_by(session_id=obj.id, status=CommandStatus.FAILED).count() == 1
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=0)))
    retry_gate(db, obj.id)
    ack(db, obj.id, GateId.GATE_IN)
    prepare_exit(db, obj.id, "30A12345")
    user = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=4)))
    checkout(db, obj.id, PaymentMethod.CASH, user)
    assert obj.status == SessionStatus.PAID_WAITING_EXIT
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 1
    with pytest.raises(HTTPException):
        checkout(db, obj.id, PaymentMethod.CASH, user)
    monkeypatch.setattr(mqtt_service, "publish", MagicMock(return_value=SimpleNamespace(rc=0)))
    retry_gate(db, obj.id)
    ack(db, obj.id, GateId.GATE_OUT)
    assert obj.status == SessionStatus.COMPLETED
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 1


def test_exit_mismatch_requires_review_without_second_swipe(db, monkeypatch):
    events = []
    monkeypatch.setattr(websocket_manager, "broadcast_sync",
                        lambda event, data: events.append((event, data)))
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    prepare_exit(db, obj.id, "29B99999")
    assert obj.status == SessionStatus.PENDING_REVIEW
    assert obj.exit_time is None
    alert = db.query(Alert).filter_by(session_id=obj.id, alert_type=AlertType.PLATE_MISMATCH).one()
    assert alert.status.value == "NEW"
    assert any(event == "NEW_ALERT" and data["alert_id"] == alert.id
               for event, data in events)
    user = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    with pytest.raises(HTTPException) as error:
        approve_exit(db, obj.id, True, "Checked photo", None, user)
    assert error.value.status_code == 400
    approve_exit(db, obj.id, True, "Checked photo", "30A12345", user)
    assert obj.status == SessionStatus.EXIT_READY
    assert obj.exit_plate == "30A12345"
    assert alert.status.value == "RESOLVED"


def test_unknown_rfid_broadcasts_alert_once(db, monkeypatch):
    events = []
    monkeypatch.setattr(websocket_manager, "broadcast_sync",
                        lambda event, data: events.append((event, data)))
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid"
    payload = {"token": device.auth_token, "cardUid": "UNKNOWN_DEMO_CARD", "gate": "GATE_IN"}
    iot_listener.handle_rfid(topic, payload)
    iot_listener.handle_rfid(topic, payload)
    db.rollback()
    assert db.query(Alert).filter_by(alert_type=AlertType.UNAUTHORIZED_CARD).count() == 1
    assert sum(event == "NEW_ALERT" for event, _ in events) == 1


def test_telemetry_keeps_occupied_slot_on_stale_or_invalid_sample(db, monkeypatch):
    events = []
    monkeypatch.setattr(websocket_manager, "broadcast_sync",
                        lambda event, data: events.append((event, data)))
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry"
    base = {"token": device.auth_token, "slotId": obj.slot_id}
    now = datetime.now()
    sample_twice(topic, {**base, "distanceCm": 8,
                         "sampledAt": now.isoformat()})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.OCCUPIED
    assert any(event == "SLOT_STATUS_CHANGED" and data["status"] == "OCCUPIED"
               for event, data in events)
    count = db.query(TelemetryLog).filter_by(slot_id=obj.slot_id).count()
    iot_listener.handle_telemetry(topic, {**base, "distanceCm": 150,
                                          "sampledAt": (now - timedelta(minutes=2)).isoformat()})
    iot_listener.handle_telemetry(topic, {**base, "distanceCm": 0})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.OCCUPIED
    assert db.query(TelemetryLog).filter_by(slot_id=obj.slot_id).count() == count


def test_cancel_occupied_requires_confirmation_and_stays_occupied(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    slot = db.query(Slot).filter_by(id=obj.slot_id).one()
    slot.status = SlotStatus.OCCUPIED
    db.commit()
    user = SimpleNamespace(id=2)
    with pytest.raises(HTTPException) as error:
        cancel_entry(db, obj.id, "Checked the area", user)
    assert error.value.status_code == 409
    cancel_entry(db, obj.id, "Vehicle left the area", user, confirmed_departure=True)
    assert slot.status == SlotStatus.OCCUPIED
    with pytest.raises(HTTPException):
        create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A99988")


def test_cannot_cancel_while_gate_command_sent(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    with pytest.raises(HTTPException) as error:
        cancel_entry(db, obj.id, "Vehicle changed mind", SimpleNamespace(id=2))
    assert error.value.status_code == 409


def test_inactive_device_messages_cannot_complete_pending_gate(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    command = db.query(Command).filter_by(session_id=obj.id).one()
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    device.status = DeviceStatus.SUSPENDED
    device.is_online = False
    last_heartbeat = device.last_heartbeat_at
    db.commit()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01"
    iot_listener.handle_ack(topic + "/ack", {"token": device.auth_token,
                                                "commandId": command.id, "status": "SUCCESS"})
    iot_listener.handle_status(topic + "/status", {"token": device.auth_token,
                                                    "status": "online"})
    db.expire_all()
    assert command.status == CommandStatus.SENT
    assert obj.status == SessionStatus.ENTRY_PENDING and obj.entry_time is None
    assert device.is_online is False and device.last_heartbeat_at == last_heartbeat


def test_cancel_review_keeps_slot_until_empty_telemetry(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    prepare_exit(db, obj.id, None)
    assert obj.status == SessionStatus.PENDING_REVIEW
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    result = client.post(f"/api/v1/sessions/{obj.id}/cancel", headers=headers,
                         json={"reason": "Vehicle physically left", "confirmed_exit": True})
    assert result.status_code == 200, result.text
    db.rollback()  # End the read snapshot before observing the API's transaction.
    assert obj.status == SessionStatus.CANCELLED
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED
    with pytest.raises(HTTPException):
        create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A99988")
    db.rollback()
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    sample_twice("parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry",
                 {"token": device.auth_token, "slotId": obj.slot_id,
                  "distanceCm": 50})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.EMPTY


def test_no_early_alert_for_pending_entry(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    iot_listener.handle_telemetry("parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry",
                                  {"token": device.auth_token, "slotId": obj.slot_id,
                                   "distanceCm": 50})
    db.rollback()
    assert db.query(Alert).filter_by(session_id=obj.id).count() == 0
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED


def test_vehicle_leaving_occupied_slot_does_not_raise_missing_parking_alert(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry"
    sample_twice(topic, {"token": device.auth_token,
                         "slotId": obj.slot_id, "distanceCm": 8})
    db.rollback()
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.OCCUPIED
    obj.entry_time = datetime.now() - timedelta(minutes=5)
    db.commit()
    iot_listener.handle_telemetry(topic, {"token": device.auth_token,
                                          "slotId": obj.slot_id, "distanceCm": 50})
    db.rollback()
    assert db.query(Alert).filter_by(session_id=obj.id).count() == 0


def test_future_tariff_is_not_used_and_fee_is_fixed_at_entry(db):
    old = db.query(Tariff).filter_by(fee_type="FLAT").first()
    old.flat_rate = 23000
    db.add(Tariff(name="Future plan", fee_type="FLAT", flat_rate=99000,
                  hourly_rate=0, grace_period_mins=5, is_active=True,
                  effective_from=datetime.now() + timedelta(days=1)))
    db.commit()
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    assert float(obj.fee_amount) == 23000
    old.flat_rate = 70000
    db.commit()
    assert float(obj.fee_amount) == 23000


def test_operator_api_waits_for_both_gate_acks(db):
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = client.post("/api/v1/sessions", headers=headers,
                          json={"device_id": "ESP32_GATE_01", "card_uid": "A1B2C3D4",
                                "entry_plate": None})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["status"] == "ENTRY_PENDING" and item["entry_time"] is None
    fixed = client.patch(f"/api/v1/sessions/{item['id']}/entry-plate", headers=headers,
                         json={"plate": "30A12345", "note": "Checked image"})
    assert fixed.status_code == 200, fixed.text
    ack(db, item["id"], GateId.GATE_IN, confirm=False)
    waiting = client.get(f"/api/v1/sessions/{item['id']}", headers=headers)
    assert waiting.json()["status"] == "ENTRY_ACKED_WAITING_PASS"
    assert waiting.json()["entry_time"] is None
    entered = client.post(f"/api/v1/sessions/{item['id']}/confirm-entry", headers=headers,
                          json={"confirmed_passage": True})
    assert entered.status_code == 200 and entered.json()["status"] == "ACTIVE"
    prepared = client.post(f"/api/v1/sessions/{item['id']}/prepare-exit", headers=headers,
                           json={"exit_plate": "30A-123.45"})
    assert prepared.status_code == 200 and prepared.json()["status"] == "EXIT_READY"
    paid = client.post(f"/api/v1/sessions/{item['id']}/checkout", headers=headers,
                       json={"payment_method": "SIMULATED_MOMO"})
    assert paid.status_code == 202, paid.text
    assert paid.json()["status"] == "PAID_WAITING_EXIT" and paid.json()["exit_time"] is None
    ack(db, item["id"], GateId.GATE_OUT, confirm=False)
    waiting = client.get(f"/api/v1/sessions/{item['id']}", headers=headers)
    assert waiting.json()["status"] == "EXIT_ACKED_WAITING_PASS"
    assert waiting.json()["exit_time"] is None
    confirmed = client.post(f"/api/v1/sessions/{item['id']}/confirm-exit", headers=headers,
                            json={"confirmed_passage": True})
    assert confirmed.status_code == 200
    done = client.get(f"/api/v1/sessions/{item['id']}", headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED"


def test_ack_without_passage_keeps_slot_and_timestamps(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN, confirm=False)
    assert obj.status == SessionStatus.ENTRY_ACKED_WAITING_PASS
    assert obj.entry_time is None
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED
    with pytest.raises(HTTPException):
        prepare_exit(db, obj.id, "30A12345")
    confirm_passage(db, obj.id, GateId.GATE_IN, SimpleNamespace(id=2))
    assert obj.status == SessionStatus.ACTIVE


def test_empty_sensor_before_exit_confirmation_does_not_release_slot(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry"
    sample_twice(topic, {"token": device.auth_token, "slotId": obj.slot_id,
                         "distanceCm": 8})
    db.rollback()
    prepare_exit(db, obj.id, "30A12345")
    checkout(db, obj.id, PaymentMethod.CASH,
             SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR")))
    ack(db, obj.id, GateId.GATE_OUT, confirm=False)
    sample_twice(topic, {"token": device.auth_token, "slotId": obj.slot_id,
                         "distanceCm": 50})
    db.rollback()
    assert obj.status == SessionStatus.EXIT_ACKED_WAITING_PASS
    assert obj.exit_time is None
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.RESERVED
    with pytest.raises(HTTPException):
        create_entry(db, "ESP32_GATE_01", "E5F6G7H8", "30A99988")
    db.rollback()
    confirm_passage(db, obj.id, GateId.GATE_OUT, SimpleNamespace(id=2))
    assert db.query(Slot).filter_by(id=obj.slot_id).one().status == SlotStatus.EMPTY


def test_cancel_exit_after_payment_reuses_single_payment(db):
    user = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    prepare_exit(db, obj.id, "30A12345")
    checkout(db, obj.id, PaymentMethod.CASH, user)
    ack(db, obj.id, GateId.GATE_OUT, confirm=False)
    assert obj.exit_time is None
    cancel_exit(db, obj.id, "Xe quay đầu", user)
    assert obj.status == SessionStatus.ACTIVE and obj.is_paid
    prepare_exit(db, obj.id, "30A12345")
    assert obj.status == SessionStatus.PAID_WAITING_EXIT
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 1
    ack(db, obj.id, GateId.GATE_OUT)
    assert obj.status == SessionStatus.COMPLETED


def test_uncertain_ocr_stays_pending_and_preserves_raw_plate(db):
    obj = create_entry(db, "ESP32_GATE_01", "E5F6G7H8", None)
    record_entry_ocr(db, obj.id, "30A-999.88", "/api/v1/alpr/images/demo_12345678.jpg")
    assert obj.entry_ocr_plate == "30A-999.88"
    assert obj.entry_plate is None
    assert db.query(Command).filter_by(session_id=obj.id).count() == 0
    confirm_entry_plate(db, obj.id, "30A-999.88",
                        SimpleNamespace(id=2, username="operator",
                                        role=SimpleNamespace(value="OPERATOR")), "Ảnh rõ")
    assert obj.entry_plate == "30A-999.88"


def test_confident_rfid_ocr_still_needs_operator_and_matching_exit_has_no_mismatch_alert(db, monkeypatch):
    result = SimpleNamespace(status="success", plate_number="30A12345",
                             suggested_plate=None, needs_confirmation=False,
                             image_url="/api/v1/alpr/images/demo_12345678.jpg", image_id="demo_12345678.jpg")

    async def snapshot(payload):
        if payload.lane == "IN":
            pending = db.query(ParkingSession).filter_by(session_code=payload.session_id).one()
            assert pending.status == SessionStatus.ENTRY_PENDING
            assert db.query(Slot).filter_by(id=pending.slot_id).one().status == SlotStatus.RESERVED
        return "/api/v1/alpr/images/demo_12345678.jpg", b"\xff\xd8\xff\xd9"

    async def recognize(payload, content, content_type, lane_crop=False):
        assert lane_crop
        pending = db.query(ParkingSession).filter_by(session_code=payload.session_id).one()
        assert (pending.entry_image_url if payload.lane == "IN" else pending.exit_image_url) == "/api/v1/alpr/images/demo_12345678.jpg"
        return result

    monkeypatch.setattr(alpr_service, "snapshot", snapshot)
    monkeypatch.setattr(alpr_service, "recognize", recognize)
    obj = parking_engine.process_rfid_event("ESP32_GATE_01",
                                            {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}, db)
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert obj.entry_ocr_plate == "30A12345" and obj.entry_plate is None
    assert db.query(Command).filter_by(session_id=obj.id).count() == 0

    operator = SimpleNamespace(id=2, username="operator", role=SimpleNamespace(value="OPERATOR"))
    confirm_entry_plate(db, obj.id, "30A12345", operator, "Đã đối chiếu ảnh")
    ack(db, obj.id, GateId.GATE_IN)
    obj = parking_engine.process_rfid_event("ESP32_GATE_01",
                                            {"cardUid": "A1B2C3D4", "gate": "GATE_OUT"}, db)
    assert obj.status == SessionStatus.PENDING_REVIEW
    assert obj.exit_ocr_plate == "30A12345"
    assert db.query(Alert).filter_by(session_id=obj.id,
                                     alert_type=AlertType.PLATE_MISMATCH).count() == 0
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 0
    approve_exit(db, obj.id, True, "Nhân viên đã đối chiếu ảnh", "30A12345", operator)
    assert obj.status == SessionStatus.EXIT_READY
    checkout(db, obj.id, PaymentMethod.CASH, operator)
    assert db.query(Payment).filter_by(session_id=obj.id).count() == 1


def test_camera_capture_only_records_ocr_even_when_confident(db, monkeypatch):
    async def snapshot(payload):
        return "/api/v1/alpr/images/demo_12345678.jpg", b"\xff\xd8\xff\xd9"

    async def recognize(payload, image, content_type, lane_crop=False):
        assert lane_crop
        from app.database import SessionLocal
        with SessionLocal() as check_db:
            current = check_db.query(ParkingSession).filter_by(session_code=payload.session_id).one()
            assert (current.entry_image_url if payload.lane == "IN" else current.exit_image_url) == "/api/v1/alpr/images/demo_12345678.jpg"
            assert (current.entry_ocr_plate if payload.lane == "IN" else current.exit_ocr_plate) is None
        return CaptureResult(request_id="demo", session_id=payload.session_id,
                             lane=payload.lane, status="success",
                             image_id="demo_12345678.jpg", plate_number="30A12345",
                             confidence=0.99, needs_confirmation=False,
                             image_url="/api/v1/alpr/images/demo_12345678.jpg")

    async def preview(_lane):
        return b"\xff\xd8\xff\xd9"

    monkeypatch.setattr(alpr_service, "snapshot", snapshot)
    monkeypatch.setattr(alpr_service, "recognize", recognize)
    monkeypatch.setattr(alpr_service, "preview", preview)
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/alpr/preview?lane=IN", headers=headers).status_code == 200
    created = client.post("/api/v1/sessions", headers=headers,
                          json={"device_id": "ESP32_GATE_01", "card_uid": "A1B2C3D4"})
    item = created.json()
    captured = client.post("/api/v1/alpr/capture", headers=headers,
                           json={"lane": "IN", "session_id": item["session_code"]})
    assert captured.status_code == 200, captured.text
    db.expire_all()
    obj = db.query(ParkingSession).filter_by(id=item["id"]).one()
    assert obj.status == SessionStatus.ENTRY_PENDING
    assert obj.entry_ocr_plate == "30A12345" and obj.entry_plate is None
    assert db.query(Command).filter_by(session_id=obj.id).count() == 0

    confirm_entry_plate(db, obj.id, "30A12345",
                        SimpleNamespace(id=2, username="operator"), "Đã đối chiếu ảnh")
    ack(db, obj.id, GateId.GATE_IN)
    captured = client.post("/api/v1/alpr/capture", headers=headers,
                           json={"lane": "OUT", "session_id": item["session_code"]})
    assert captured.status_code == 200, captured.text
    db.rollback()
    assert obj.status == SessionStatus.ACTIVE
    assert obj.exit_ocr_plate == "30A12345" and obj.exit_plate is None


def test_camera_snapshot_remains_visible_when_ocr_fails(db, monkeypatch):
    async def snapshot(_payload):
        return "/api/v1/alpr/images/demo_12345678.jpg", b"\xff\xd8\xff\xd9"

    async def recognize(_payload, _image, _content_type, lane_crop=False):
        assert lane_crop
        raise HTTPException(503, "OCR timeout")

    monkeypatch.setattr(alpr_service, "snapshot", snapshot)
    monkeypatch.setattr(alpr_service, "recognize", recognize)
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = client.post("/api/v1/sessions", headers=headers,
                          json={"device_id": "ESP32_GATE_01", "card_uid": "A1B2C3D4"})
    item = created.json()
    captured = client.post("/api/v1/alpr/capture", headers=headers,
                           json={"lane": "IN", "session_id": item["session_code"]})
    assert captured.status_code == 503
    db.rollback()
    obj = db.query(ParkingSession).filter_by(id=item["id"]).one()
    assert obj.entry_image_url == "/api/v1/alpr/images/demo_12345678.jpg"
    assert obj.entry_ocr_plate is None


def test_manual_exit_keeps_camera_ocr_separate_from_confirmed_plate(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    ack(db, obj.id, GateId.GATE_IN)
    obj.exit_ocr_plate = "30A1234S"
    obj.exit_image_url = "/api/v1/alpr/images/demo_12345678.jpg"
    db.commit()
    prepare_exit(db, obj.id, "30A12345", user=SimpleNamespace(id=2))
    assert obj.exit_ocr_plate == "30A1234S"
    assert obj.exit_plate == "30A12345"
    assert obj.exit_image_url.endswith("demo_12345678.jpg")


def test_unknown_slot_requires_two_fresh_samples_to_recover(db):
    obj = create_entry(db, "ESP32_GATE_01", "A1B2C3D4", None)
    slot = db.query(Slot).filter_by(id=obj.slot_id).one()
    slot.last_valid_sample_at = datetime.now() - timedelta(seconds=40)
    db.commit()
    assert iot_listener.expire_stale_slots(30) >= 1
    db.rollback()
    assert slot.status == SlotStatus.UNKNOWN
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    topic = "parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry"
    sample = {"token": device.auth_token, "slotId": obj.slot_id, "distanceCm": 50}
    iot_listener.handle_telemetry(topic, sample)
    db.rollback()
    assert slot.status == SlotStatus.UNKNOWN
    iot_listener.handle_telemetry(topic, sample)
    db.rollback()
    assert slot.status == SlotStatus.RESERVED


def test_lcd_feedback_for_unknown_card_and_barrier_contract(db, monkeypatch):
    captured = []
    monkeypatch.setattr(mqtt_service, "publish", lambda topic, payload, **opts:
                        (captured.append((topic, payload, opts)) or SimpleNamespace(rc=0)))
    device = db.query(Device).filter_by(id="ESP32_GATE_01").one()
    iot_listener.handle_rfid("parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid",
                             {"token": device.auth_token, "cardUid": "BAD_CARD",
                              "gate": "GATE_IN", "eventId": "rfid-1"})
    assert any(t.endswith("/feedback") and p["code"] == "INVALID_CARD"
               and o.get("retain") is False for t, p, o in captured)
    create_entry(db, "ESP32_GATE_01", "A1B2C3D4", "30A12345")
    assert any(t.endswith("/commands") and p.get("autoCloseMs") == 30000
               for t, p, _ in captured)
