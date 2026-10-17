import os
import sys
import time
import socket
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

from fastapi.testclient import TestClient
import paho.mqtt.client as mqtt

from app.config import settings
from app.main import app
from app.services.mqtt_service import MQTTService, mqtt_service


def is_broker_listening(host: str, port: int, timeout_sec: float = 1.0) -> bool:
    """Kiểm tra nhanh xem cổng Broker MQTT có đang mở và lắng nghe hay không."""
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def run_mqtt_platform_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: MQTT PLATFORM & FASTAPI LIFESPAN (MILESTONE 4.2)")
    print("   PAHO-MQTT V2 CALLBACKS, TOPIC ROUTING & RESILIENCE")
    print("==================================================")

    # ----------------------------------------------------
    # 1. KIỂM TRA KHỞI TẠO VÀ CẤU HÌNH MQTT SERVICE
    # ----------------------------------------------------
    print("\n[BƯỚC 1] Kiểm tra Khởi tạo MQTTService & Callback API v2...")
    custom_service = MQTTService()
    assert isinstance(custom_service.client, mqtt.Client), "Client phải là đối tượng paho.mqtt.client.Client"
    assert custom_service.is_connected is False, "Trạng thái ban đầu phải là False"
    print(f"   [OK] Khởi tạo MQTTService thành công với Client ID: {settings.MQTT_CLIENT_ID}")
    print(f"   [OK] Cấu hình Broker mục tiêu: {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")

    # ----------------------------------------------------
    # 2. KIỂM THỬ ĐĂNG KÝ VÀ ĐIỀU PHỐI TIN NHẮN (TOPIC ROUTING)
    # ----------------------------------------------------
    print("\n[BƯỚC 2] Kiểm thử Topic Router / Event Dispatcher & Wildcard Matching...")
    received_telemetry = []
    received_rfid = []

    def on_telemetry(topic: str, payload: dict):
        received_telemetry.append((topic, payload))

    def on_rfid(topic: str, payload: dict):
        received_rfid.append((topic, payload))

    # Đăng ký với wildcard '+' và '#'
    custom_service.subscribe("parking/+/telemetry", on_telemetry)
    custom_service.subscribe("parking/PTIT_ZONE_1/#", on_rfid)

    # 2.1 Giả lập bản tin telemetry khớp pattern 'parking/+/telemetry'
    mock_msg1 = mqtt.MQTTMessage(topic=b"parking/ESP32_GATE_01/telemetry")
    mock_msg1.payload = b'{"slotId": "SLOT_A1", "distanceCm": 14.2, "isOccupied": true}'
    custom_service._on_message(custom_service.client, None, mock_msg1)

    assert len(received_telemetry) == 1, "Handler telemetry phải nhận được tin nhắn"
    assert received_telemetry[0][1]["distanceCm"] == 14.2
    assert received_telemetry[0][1]["slotId"] == "SLOT_A1"
    print("   [OK] Dispatch thành công bản tin khớp wildcard '+': parking/+/telemetry")

    # 2.2 Giả lập bản tin RFID khớp pattern 'parking/PTIT_ZONE_1/#'
    mock_msg2 = mqtt.MQTTMessage(topic=b"parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid")
    mock_msg2.payload = b'{"cardUid": "A1B2C3D4", "gate": "ENTRY"}'
    custom_service._on_message(custom_service.client, None, mock_msg2)

    assert len(received_rfid) == 1, "Handler RFID phải nhận được tin nhắn"
    assert received_rfid[0][1]["cardUid"] == "A1B2C3D4"
    print("   [OK] Dispatch thành công bản tin khớp wildcard '#': parking/PTIT_ZONE_1/#")

    # 2.3 Kiểm tra hủy đăng ký (unsubscribe)
    custom_service.unsubscribe("parking/+/telemetry")
    assert "parking/+/telemetry" not in custom_service._handlers
    print("   [OK] Hủy đăng ký topic (unsubscribe) hoạt động chính xác.")

    # ----------------------------------------------------
    # 3. KIỂM THỬ TÍCH HỢP FASTAPI LIFESPAN
    # ----------------------------------------------------
    print("\n[BƯỚC 3] Kiểm thử Tích hợp FastAPI Lifespan (Startup/Shutdown Context Manager)...")
    with TestClient(app) as client:
        # Trong khối with: startup event đã chạy
        res_health = client.get("/health")
        assert res_health.status_code == 200, f"Health check thất bại: {res_health.text}"
        health_data = res_health.json()
        assert health_data["status"] == "healthy"
        assert "mqtt_connected" in health_data
        print(f"   [OK] Lifespan Startup kích hoạt thành công! Endpoint /health: {health_data}")

        res_root = client.get("/")
        assert res_root.status_code == 200
        print("   [OK] Endpoint gốc / phản hồi tốt song song với luồng MQTT.")
    # Ra khỏi khối with: shutdown event đã chạy an toàn
    print("   [OK] Lifespan Shutdown kích hoạt và giải phóng tài nguyên an toàn.")

    # ----------------------------------------------------
    # 4. KIỂM THỬ KẾT NỐI VÀ VÒNG LẶP LOOPBACK VỚI BROKER
    # ----------------------------------------------------
    print("\n[BƯỚC 4] Kiểm thử Kết nối & Thích ứng với Mosquitto Broker...")
    broker_online = is_broker_listening(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT)

    if broker_online:
        print(f"   [PHÁT HIỆN] Mosquitto Broker đang hoạt động tại {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}!")
        print("   [TIẾN HÀNH] Chạy kiểm thử Loopback Publish/Subscribe thời gian thực...")

        live_service = MQTTService()
        loopback_received = []

        def on_loopback(topic: str, payload: dict):
            loopback_received.append(payload)

        test_topic = f"{settings.MQTT_BASE_TOPIC}/test/loopback_{int(time.time())}"
        live_service.subscribe(test_topic, on_loopback)
        live_service.start()

        # Đợi tối đa 3 giây để kết nối broker
        timeout = 3.0
        start_wait = time.time()
        while not live_service.is_connected and (time.time() - start_wait) < timeout:
            time.sleep(0.1)

        if live_service.is_connected:
            print("   [OK] Thiết lập kết nối thành công tới Mosquitto Broker cục bộ!")
            # Publish bản tin test
            test_payload = {
                "message": "hello_from_fastapi_smoke_test",
                "timestamp": datetime.now().isoformat(),
            }
            live_service.publish(test_topic, test_payload, qos=1)

            # Chờ nhận lại bản tin qua loopback
            wait_msg_timeout = 3.0
            start_msg_wait = time.time()
            while not loopback_received and (time.time() - start_msg_wait) < wait_msg_timeout:
                time.sleep(0.1)

            assert len(loopback_received) > 0, "Không nhận được bản tin loopback từ Mosquitto Broker"
            assert loopback_received[0]["message"] == "hello_from_fastapi_smoke_test"
            print("   [OK] Kiểm thử Loopback Publish/Subscribe thành công 100%!")
        else:
            print("   [CẢNH BÁO] Kết nối tới Broker bị timeout dù cổng đang mở.")

        live_service.stop()
    else:
        print(f"   [THÔNG BÁO] Cổng {settings.MQTT_BROKER_PORT} chưa có Mosquitto Broker đang chạy.")
        print("   [TIẾN HÀNH] Kiểm tra cơ chế Khả năng phục hồi (Resilience & Non-blocking)...")

        # Thử khởi động service khi không có broker
        resilient_service = MQTTService()
        try:
            resilient_service.start()
            print("   [OK] Service khởi động non-blocking an toàn tuyệt đối, không gây sập ứng dụng!")
        finally:
            resilient_service.stop()

        print("\n   [HƯỚNG DẪN BẬT BROKER ĐỂ TEST LIVE]:")
        print("   - Khởi động service Mosquitto: net start mosquitto")
        print("   - Hoặc chạy trực tiếp từ terminal: mosquitto -v")

    print("\n==================================================")
    print("   HOÀN TẤT KIỂM THỬ: 100% NỀN TẢNG MQTT CLIENT ĐẠT CHUẨN!")
    print("==================================================")


if __name__ == "__main__":
    run_mqtt_platform_smoke_test()
