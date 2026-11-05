"""Tests for Core Parking Engine (Milestone 5.2)."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.database import SessionLocal
from app.models.enums import (
    CardType,
    CommandAction,
    GateId,
    CommandStatus,
    DeviceStatus,
    PaymentMethod,
    PaymentStatus,
    SessionStatus,
    SlotStatus,
    AlertType,
    AlertSeverity,
)
from app.models.device import Device
from app.models.card import Card
from app.models.slot import Slot
from app.models.session import ParkingSession
from app.models.payment import Payment
from app.models.command import Command
from app.models.alert import Alert
from app.services.parking_engine import parking_engine
from app.services.mqtt_service import mqtt_service


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        # Đảm bảo thiết bị test đang online và có nhịp tim mới
        dev = session.query(Device).filter(Device.id == "ESP32_GATE_01").first()
        if dev:
            dev.is_online = True
            dev.last_heartbeat_at = datetime.now()
            dev.status = DeviceStatus.ACTIVATED
            session.commit()
        yield session
    finally:
        session.close()


def test_entry_swipe_valid_creates_session_and_opens_barrier(db, monkeypatch):
    """Quẹt thẻ vào hợp lệ: Tạo phiên ACTIVE, slot_id=None, phát lệnh mở barrier cổng vào."""
    # Mock MQTT publish
    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    # Mock ALPR trả về biển số hợp lệ
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-888.99", "/api/v1/alpr/images/entry.jpg")):
        payload = {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}
        session_obj = parking_engine.process_rfid_event("ESP32_GATE_01", payload, db)

    assert session_obj is not None
    assert session_obj.status == SessionStatus.ACTIVE
    assert session_obj.card_uid == "A1B2C3D4"
    assert session_obj.entry_plate == "30A-888.99"
    assert session_obj.slot_id is None
    assert session_obj.fee_amount == 20000.0

    # Kiểm tra lệnh mở barrier đã được ghi vào bảng commands
    cmd = (
        db.query(Command)
        .filter(Command.device_id == "ESP32_GATE_01", Command.gate_id == GateId.GATE_IN)
        .order_by(Command.sent_at.desc())
        .first()
    )
    assert cmd is not None
    assert cmd.action == CommandAction.OPEN
    assert cmd.status == CommandStatus.SENT
    assert cmd.sent_by_user_id is None  # Lệnh tự động từ hệ thống


def test_entry_swipe_double_entry_rejected(db, monkeypatch):
    """Chống quẹt đúp: Thẻ đang có phiên ACTIVE quẹt tiếp bị từ chối và sinh cảnh báo."""
    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    # Lần 1: Quẹt vào thành công
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-888.99", None)):
        payload = {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}
        s1 = parking_engine.process_rfid_event("ESP32_GATE_01", payload, db)
    assert s1 is not None

    # Lần 2: Quẹt tiếp lần nữa
    cmd_count_before = db.query(Command).count()
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-888.99", None)):
        s2 = parking_engine.process_rfid_event("ESP32_GATE_01", payload, db)

    assert s2 is None
    # Không phát thêm lệnh mở barrier mới
    assert db.query(Command).count() == cmd_count_before

    # Kiểm tra cảnh báo quẹt đúp được sinh ra
    alert = (
        db.query(Alert)
        .filter(Alert.device_id == "ESP32_GATE_01", Alert.alert_type == AlertType.SYSTEM_ERROR)
        .order_by(Alert.created_at.desc())
        .first()
    )
    assert alert is not None
    assert "quẹt đúp" in alert.message


def test_entry_swipe_full_lot_rejected(db, monkeypatch):
    """Bãi xe đầy: Khi không còn ô EMPTY, quẹt thẻ vào bị từ chối và barrier giữ đóng."""
    # Đặt toàn bộ ô đỗ sang OCCUPIED
    db.query(Slot).update({Slot.status: SlotStatus.OCCUPIED})
    db.commit()

    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    cmd_count_before = db.query(Command).count()
    payload = {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}
    result = parking_engine.process_rfid_event("ESP32_GATE_01", payload, db)

    assert result is None
    # Không có lệnh mở barrier nào được phát
    assert db.query(Command).count() == cmd_count_before

    # Kiểm tra cảnh báo bãi đầy
    alert = (
        db.query(Alert)
        .filter(Alert.device_id == "ESP32_GATE_01", Alert.alert_type == AlertType.SYSTEM_ERROR)
        .order_by(Alert.created_at.desc())
        .first()
    )
    assert alert is not None
    assert "đã đầy" in alert.message


def test_exit_swipe_plate_match_auto_checkout(db, monkeypatch):
    """Quẹt thẻ ra với biển số khớp 100%: Tự động thu phí 20.000đ, COMPLETED phiên và mở barrier ra."""
    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    # 1. Xe vào
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-999.88", "/api/v1/alpr/images/in.jpg")):
        s_in = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}, db)
    assert s_in is not None

    # 2. Xe quẹt thẻ ra với cùng biển số
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-999.88", "/api/v1/alpr/images/out.jpg")):
        s_out = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "A1B2C3D4", "gate": "GATE_OUT"}, db)

    assert s_out is not None
    assert s_out.status == SessionStatus.COMPLETED
    assert s_out.is_paid is True
    assert s_out.exit_plate == "30A-999.88"
    assert s_out.exit_time is not None

    # Kiểm tra Payment được tạo thành công
    pmt = db.query(Payment).filter(Payment.session_id == s_out.id).first()
    assert pmt is not None
    assert pmt.amount == 20000.0
    assert pmt.payment_status == PaymentStatus.PAID
    assert pmt.payment_method == PaymentMethod.CASH

    # Kiểm tra lệnh mở barrier GATE_OUT
    cmd = (
        db.query(Command)
        .filter(Command.device_id == "ESP32_GATE_01", Command.gate_id == GateId.GATE_OUT)
        .order_by(Command.sent_at.desc())
        .first()
    )
    assert cmd is not None
    assert cmd.action == CommandAction.OPEN
    assert cmd.status == CommandStatus.SENT


def test_exit_swipe_plate_mismatch_pending_review(db, monkeypatch):
    """Quẹt thẻ ra lệch biển số: Chuyển PENDING_REVIEW, barrier giữ đóng, sinh cảnh báo CRITICAL."""
    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    # 1. Xe vào với biển 30A-111.11
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("30A-111.11", None)):
        s_in = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "A1B2C3D4", "gate": "GATE_IN"}, db)
    assert s_in is not None

    # Đếm số lệnh GATE_OUT trước khi quẹt ra
    gate_out_cmds_before = db.query(Command).filter(Command.gate_id == GateId.GATE_OUT).count()

    # 2. Xe ra với biển 29B-999.99 (Lệch biển số)
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("29B-999.99", "/api/v1/alpr/images/mismatch.jpg")):
        s_out = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "A1B2C3D4", "gate": "GATE_OUT"}, db)

    assert s_out is not None
    assert s_out.status == SessionStatus.PENDING_REVIEW
    assert s_out.is_paid is False
    assert s_out.exit_plate == "29B-999.99"
    assert "Lệch" in s_out.review_reason

    # Barrier GIỮ ĐÓNG: Không phát lệnh GATE_OUT nào
    assert db.query(Command).filter(Command.gate_id == GateId.GATE_OUT).count() == gate_out_cmds_before

    # Kiểm tra cảnh báo CRITICAL được sinh ra
    alert = (
        db.query(Alert)
        .filter(Alert.device_id == "ESP32_GATE_01", Alert.alert_type == AlertType.PLATE_MISMATCH)
        .order_by(Alert.created_at.desc())
        .first()
    )
    assert alert is not None
    assert alert.severity == AlertSeverity.CRITICAL


def test_exit_swipe_monthly_card_zero_fee(db, monkeypatch):
    """Thẻ tháng quẹt ra khớp biển: Thu phí 0đ, phiên COMPLETED và mở barrier ra."""
    mock_publish = MagicMock(return_value=MagicMock(rc=0))
    monkeypatch.setattr(mqtt_service, "publish", mock_publish)

    # 1. Xe thẻ tháng vào
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("29A-678.90", None)):
        s_in = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "E5F6G7H8", "gate": "GATE_IN"}, db)
    assert s_in is not None

    # 2. Xe thẻ tháng ra
    with patch.object(parking_engine, "_call_alpr_capture", return_value=("29A-678.90", None)):
        s_out = parking_engine.process_rfid_event("ESP32_GATE_01", {"cardUid": "E5F6G7H8", "gate": "GATE_OUT"}, db)

    assert s_out is not None
    assert s_out.status == SessionStatus.COMPLETED
    assert s_out.fee_amount == 0.00
    assert s_out.is_paid is True

    # Bản ghi Payment mang giá trị 0đ
    pmt = db.query(Payment).filter(Payment.session_id == s_out.id).first()
    assert pmt is not None
    assert pmt.amount == 0.00
