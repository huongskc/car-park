import os
import sys
from datetime import datetime

# Cấu hình UTF-8 cho console Windows tránh UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo đường dẫn import app package luôn đúng
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import paho.mqtt.client as mqtt

from app.database import SessionLocal
from app.models.enums import DeviceStatus, SlotStatus, CardType, AlertType, AlertSeverity
from app.models.device import Device, DeviceConfig
from app.models.slot import Slot
from app.models.card import Card
from app.models.telemetry import TelemetryLog
from app.models.alert import Alert
from app.services.mqtt_service import MQTTService
from app.services.iot_listener import iot_listener, setup_iot_listeners, parse_topic_info


TEST_DEVICE_ID = "ESP32_TEST_LISTENER_01"
TEST_SLOT_ID = "SLOT_TEST_L1"
TEST_CARD_VALID = "CARD_TEST_VALID_01"
TEST_CARD_LOCKED = "CARD_TEST_LOCKED_02"


def cleanup_test_data(db):
    """Dọn dẹp triệt để dữ liệu sinh ra trong quá trình kiểm thử."""
    try:
        # Xóa alerts
        db.query(Alert).filter(Alert.device_id == TEST_DEVICE_ID).delete(synchronize_session=False)
        # Xóa telemetry_logs
        db.query(TelemetryLog).filter(TelemetryLog.device_id == TEST_DEVICE_ID).delete(synchronize_session=False)
        # Xóa slot
        db.query(Slot).filter(Slot.id == TEST_SLOT_ID).delete(synchronize_session=False)
        # Xóa cards
        db.query(Card).filter(Card.uid.in_([TEST_CARD_VALID, TEST_CARD_LOCKED])).delete(synchronize_session=False)
        # Xóa device config & device
        db.query(DeviceConfig).filter(DeviceConfig.device_id == TEST_DEVICE_ID).delete(synchronize_session=False)
        db.query(Device).filter(Device.id == TEST_DEVICE_ID).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"   [CẢNH BÁO] Không thể dọn dẹp dữ liệu test: {e}")


def seed_test_data(db):
    """Nạp dữ liệu mẫu ban đầu cho kịch bản test."""
    # 1. Tạo thiết bị ACTIVATED
    dev = Device(
        id=TEST_DEVICE_ID,
        name="ESP32 Test Listener Station",
        site_id="PTIT_ZONE_1",
        auth_token="dtk_test_listener_token",
        status=DeviceStatus.ACTIVATED,
        is_online=False,
    )
    db.add(dev)

    # 2. Tạo config thiết bị với ngưỡng khoảng cách 15.0 cm
    cfg = DeviceConfig(
        device_id=TEST_DEVICE_ID,
        distance_threshold_cm=15.00,
        servo_open_time_sec=10,
        heartbeat_interval_sec=10,
        buzzer_enabled=True,
        version=1,
    )
    db.add(cfg)

    # 3. Tạo ô đỗ test gắn với thiết bị
    slot = Slot(
        id=TEST_SLOT_ID,
        name="Ô đỗ Test Listener L1",
        site_id="PTIT_ZONE_1",
        device_id=TEST_DEVICE_ID,
        status=SlotStatus.EMPTY,
    )
    db.add(slot)

    # 4. Tạo thẻ hợp lệ và thẻ bị khóa
    card_ok = Card(
        uid=TEST_CARD_VALID,
        card_type=CardType.MONTHLY,
        owner_name="Tester Nguyen",
        registered_plate="29A-12345",
        is_active=True,
    )
    card_bad = Card(
        uid=TEST_CARD_LOCKED,
        card_type=CardType.VISITOR,
        owner_name="Locked Visitor",
        registered_plate="30B-99999",
        is_active=False,
    )
    db.add_all([card_ok, card_bad])

    db.commit()


def run_iot_listener_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: IOT MESSAGE LISTENER & DB SYNC (MILESTONE 4.3)")
    print("   TELEMETRY, HEARTBEAT, SLOTS REALTIME & RFID EVENTS")
    print("==================================================")

    db = SessionLocal()
    try:
        # Dọn dẹp trước
        cleanup_test_data(db)
        seed_test_data(db)

        # ----------------------------------------------------
        # 1. KIỂM THỬ BÓC TÁCH TOPIC (TOPIC PARSER)
        # ----------------------------------------------------
        print("\n[BƯỚC 1] Kiểm tra Bóc tách Topic (Topic Parser)...")
        s, d, m = parse_topic_info("parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry")
        assert (s, d, m) == ("PTIT_ZONE_1", "ESP32_GATE_01", "telemetry")

        s, d, m = parse_topic_info("parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid")
        assert (s, d, m) == ("PTIT_ZONE_1", "ESP32_GATE_01", "events/rfid")

        s, d, m = parse_topic_info("parking/devices/ESP32_GATE_01/status")
        assert (d, m) == ("ESP32_GATE_01", "status")

        s, d, m = parse_topic_info("parking/ESP32_GATE_01/status")
        assert (d, m) == ("ESP32_GATE_01", "status")
        print("   [OK] Bóc tách chính xác cả 2 chuẩn Topic: 3 cấp phân vùng và 2 cấp rút gọn.")

        # ----------------------------------------------------
        # 2. KIỂM THỬ XỬ LÝ HEARTBEAT & STATUS
        # ----------------------------------------------------
        print("\n[BƯỚC 2] Kiểm thử Nhận Bản tin Status / Heartbeat...")
        status_topic = f"parking/PTIT_ZONE_1/{TEST_DEVICE_ID}/status"
        status_payload = {
            "token": "dtk_test_listener_token",
            "isOnline": True,
            "fwVersion": "1.2.0-beta",
            "ipAddress": "192.168.1.188",
        }
        iot_listener.handle_status(status_topic, status_payload)

        # Kiểm tra CSDL
        db.commit()
        dev = db.query(Device).filter(Device.id == TEST_DEVICE_ID).first()
        assert dev.is_online is True
        assert dev.fw_version == "1.2.0-beta"
        assert dev.ip_address == "192.168.1.188"
        assert dev.last_heartbeat_at is not None
        print(f"   [OK] Thiết bị '{dev.id}' đã cập nhật online = True, FW = {dev.fw_version}, IP = {dev.ip_address}")

        # ----------------------------------------------------
        # 3. KIỂM THỬ TELEMETRY: XE VÀO ĐỖ (EMPTY -> OCCUPIED)
        # ----------------------------------------------------
        print("\n[BƯỚC 3] Kiểm thử Telemetry: Cảm biến phát hiện có xe (khoảng cách 11.2 cm <= 15.0 cm)...")
        telem_topic = f"parking/PTIT_ZONE_1/{TEST_DEVICE_ID}/telemetry"
        telem_payload_occupied = {
            "token": "dtk_test_listener_token",
            "slotId": TEST_SLOT_ID,
            "distanceCm": 11.2,
            "bootId": "boot_101",
            "seq": 1,
            "sampledAt": datetime.now().isoformat(),
        }
        iot_listener.handle_telemetry(telem_topic, telem_payload_occupied)

        # Đối soát bảng slots và telemetry_logs
        db.commit()
        slot = db.query(Slot).filter(Slot.id == TEST_SLOT_ID).first()
        assert slot.status == SlotStatus.OCCUPIED, "Ô đỗ phải chuyển sang OCCUPIED"
        assert float(slot.last_distance_cm) == 11.2

        t_log = (
            db.query(TelemetryLog)
            .filter(TelemetryLog.device_id == TEST_DEVICE_ID, TelemetryLog.slot_id == TEST_SLOT_ID)
            .order_by(TelemetryLog.id.desc())
            .first()
        )
        assert t_log is not None
        assert t_log.is_occupied is True
        assert float(t_log.distance_cm) == 11.2
        print(f"   [OK] Ô đỗ '{slot.id}' chuyển sang OCCUPIED thành công! Đã lưu bản ghi TelemetryLog ID={t_log.id}.")

        # ----------------------------------------------------
        # 4. KIỂM THỬ TELEMETRY: XE RỜI Ô (OCCUPIED -> EMPTY)
        # ----------------------------------------------------
        print("\n[BƯỚC 4] Kiểm thử Telemetry: Cảm biến phát hiện xe rời đi (khoảng cách 28.5 cm > 15.0 cm)...")
        telem_payload_empty = {
            "token": "dtk_test_listener_token",
            "slotId": TEST_SLOT_ID,
            "distanceCm": 28.5,
            "bootId": "boot_101",
            "seq": 2,
            "sampledAt": datetime.now().isoformat(),
        }
        iot_listener.handle_telemetry(telem_topic, telem_payload_empty)

        db.commit()
        slot = db.query(Slot).filter(Slot.id == TEST_SLOT_ID).first()
        assert slot.status == SlotStatus.EMPTY, "Ô đỗ phải chuyển về EMPTY"
        assert float(slot.last_distance_cm) == 28.5
        print(f"   [OK] Ô đỗ '{slot.id}' giải phóng về EMPTY thành công!")

        # ----------------------------------------------------
        # 5. KIỂM THỬ CHẶN THIẾT BỊ CHƯA KÍCH HOẠT (VÒNG ĐỜI)
        # ----------------------------------------------------
        print("\n[BƯỚC 5] Kiểm thử Chặn Thiết bị Chưa Kích hoạt (SUSPENDED) & Tự động tạo Alert...")
        # Tạm ngưng thiết bị
        dev.status = DeviceStatus.SUSPENDED
        db.commit()

        iot_listener.handle_telemetry(telem_topic, telem_payload_occupied)

        # Ô đỗ không được phép đổi trạng thái
        db.commit()
        slot_check = db.query(Slot).filter(Slot.id == TEST_SLOT_ID).first()
        assert slot_check.status == SlotStatus.EMPTY, "Ô đỗ không được đổi trạng thái khi thiết bị SUSPENDED"

        # Kiểm tra CSDL có cảnh báo sự cố không
        alert_dev = (
            db.query(Alert)
            .filter(Alert.device_id == TEST_DEVICE_ID, Alert.alert_type == AlertType.SYSTEM_ERROR)
            .order_by(Alert.id.desc())
            .first()
        )
        assert alert_dev is not None, "Phải sinh bản ghi cảnh báo SYSTEM_ERROR"
        print(f"   [OK] Chặn thiết bị SUSPENDED thành công! Cảnh báo tự sinh: '{alert_dev.message}'")

        # Khôi phục trạng thái ACTIVATED
        dev.status = DeviceStatus.ACTIVATED
        db.commit()

        # ----------------------------------------------------
        # 6. KIỂM THỬ BẢO LƯU TRẠNG THÁI Ô ĐỖ BẢO TRÌ (MAINTENANCE)
        # ----------------------------------------------------
        print("\n[BƯỚC 6] Kiểm thử Bảo lưu Ô đỗ Bảo trì (MAINTENANCE) & Cảnh báo Vi phạm Đỗ xe...")
        slot.status = SlotStatus.MAINTENANCE
        db.commit()

        # Bắn khoảng cách báo có xe đỗ
        iot_listener.handle_telemetry(telem_topic, telem_payload_occupied)

        db.commit()
        slot_maint = db.query(Slot).filter(Slot.id == TEST_SLOT_ID).first()
        assert slot_maint.status == SlotStatus.MAINTENANCE, "Ô đỗ phải bảo lưu trạng thái MAINTENANCE"

        alert_maint = (
            db.query(Alert)
            .filter(Alert.device_id == TEST_DEVICE_ID, Alert.message.like("%ô đang bảo trì%"))
            .first()
        )
        assert alert_maint is not None, "Phải sinh cảnh báo đỗ xe trái phép tại ô bảo trì"
        print(f"   [OK] Bảo lưu MAINTENANCE thành công và phát hiện vi phạm: '{alert_maint.message}'")

        # ----------------------------------------------------
        # 7. KIỂM THỬ SỰ KIỆN QUẸT THẺ RFID & ALERT KHI VI PHẠM
        # ----------------------------------------------------
        print("\n[BƯỚC 7] Kiểm thử Sự kiện Quẹt thẻ RFID & Cảnh báo Thẻ vi phạm...")
        rfid_topic = f"parking/PTIT_ZONE_1/{TEST_DEVICE_ID}/events/rfid"

        # 7.1 Thẻ không tồn tại
        iot_listener.handle_rfid(rfid_topic, {"token": "dtk_test_listener_token", "cardUid": "CARD_UNKNOWN_99", "gate": "ENTRY"})
        db.commit()
        alert_unknown = (
            db.query(Alert)
            .filter(Alert.alert_type == AlertType.UNAUTHORIZED_CARD, Alert.message.like("%CARD_UNKNOWN_99%"))
            .first()
        )
        assert alert_unknown is not None
        print(f"   [OK] Phát hiện thẻ không tồn tại: '{alert_unknown.message}'")

        # 7.2 Thẻ bị khóa
        iot_listener.handle_rfid(rfid_topic, {"token": "dtk_test_listener_token", "cardUid": TEST_CARD_LOCKED, "gate": "ENTRY"})
        db.commit()
        alert_locked = (
            db.query(Alert)
            .filter(Alert.alert_type == AlertType.UNAUTHORIZED_CARD, Alert.message.like(f"%{TEST_CARD_LOCKED}%"))
            .first()
        )
        assert alert_locked is not None
        print(f"   [OK] Phát hiện thẻ đã bị khóa: '{alert_locked.message}'")

        # 7.3 Thẻ hợp lệ & kích hoạt Hook
        hook_called = []

        def sample_hook(dev_id: str, payload: dict, s):
            hook_called.append((dev_id, payload))

        iot_listener.register_rfid_hook(sample_hook)
        iot_listener.handle_rfid(rfid_topic, {"token": "dtk_test_listener_token", "cardUid": TEST_CARD_VALID, "gate": "ENTRY"})
        assert len(hook_called) == 1
        assert hook_called[0][1]["cardUid"] == TEST_CARD_VALID
        print("   [OK] Thẻ hợp lệ được chấp nhận và kích hoạt thành công hook xử lý tiếp theo!")

        # ----------------------------------------------------
        # 8. KIỂM THỬ TÍCH HỢP MQTT DISPATCHING VỚI SETUP_LISTENERS
        # ----------------------------------------------------
        print("\n[BƯỚC 8] Kiểm thử Tích hợp MQTT Service Dispatching...")
        mock_mqtt = MQTTService()
        setup_iot_listeners(mock_mqtt)

        # Giả lập bắn message qua paho-mqtt mock
        mock_msg = mqtt.MQTTMessage(topic=f"parking/PTIT_ZONE_1/{TEST_DEVICE_ID}/status".encode())
        mock_msg.payload = b'{"token": "dtk_test_listener_token", "isOnline": true, "fwVersion": "2.0.0"}'
        mock_mqtt._on_message(mock_mqtt.client, None, mock_msg)

        db.commit()
        dev_final = db.query(Device).filter(Device.id == TEST_DEVICE_ID).first()
        assert dev_final.fw_version == "2.0.0"
        print("   [OK] Dispatching qua MQTT Service hoạt động trơn tru 100%!")

        print("\n==================================================")
        print("   HOÀN TẤT KIỂM THỬ: 100% IOT LISTENER ENGINE ĐẠT CHUẨN!")
        print("==================================================")

    finally:
        cleanup_test_data(db)
        db.close()


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_iot_listener_smoke_test()
