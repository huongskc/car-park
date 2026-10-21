import os
import sys
import json
import time

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

from app.database import SessionLocal
from app.main import app
from app.models.enums import DeviceStatus, CommandAction, CommandStatus, GateId
from app.models.device import Device, DeviceConfig
from app.models.command import Command
from app.models.audit import AuditLog
from app.services.mqtt_service import mqtt_service
from app.services.iot_listener import iot_listener, setup_iot_listeners


TEST_DEVICE_ID = "ESP32_CMD_TEST_GATE_01"
TEST_SITE_ID = "PTIT_ZONE_1"


class FakeMqttMessage:
    """Mock đối tượng bản tin MQTT của thư viện paho-mqtt để kiểm thử listener."""
    def __init__(self, topic: str, payload_dict: dict):
        self.topic = topic
        self.payload = json.dumps(payload_dict).encode("utf-8")


def cleanup_test_data(db):
    """Dọn dẹp triệt để dữ liệu sinh ra trong quá trình kiểm thử lệnh điều khiển."""
    try:
        # Xóa commands
        db.query(Command).filter(Command.device_id == TEST_DEVICE_ID).delete(synchronize_session=False)
        # Xóa audit logs liên quan đến thiết bị test
        db.query(AuditLog).filter(
            AuditLog.resource_type == "devices",
            AuditLog.resource_id == TEST_DEVICE_ID,
        ).delete(synchronize_session=False)
        # Xóa device config & device
        db.query(DeviceConfig).filter(DeviceConfig.device_id == TEST_DEVICE_ID).delete(synchronize_session=False)
        db.query(Device).filter(Device.id == TEST_DEVICE_ID).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"   [CẢNH BÁO] Không thể dọn dẹp dữ liệu test: {e}")


def seed_test_data(db):
    """Tạo thiết bị test ban đầu ở trạng thái sẵn sàng nhận lệnh."""
    dev = Device(
        id=TEST_DEVICE_ID,
        name="ESP32 Barrier Test Gate 01",
        site_id=TEST_SITE_ID,
        auth_token="dtk_test_cmd_token_123",
        status=DeviceStatus.ACTIVATED,
        is_online=True,
    )
    db.add(dev)

    cfg = DeviceConfig(
        device_id=TEST_DEVICE_ID,
        distance_threshold_cm=15.00,
        servo_open_time_sec=10,
        heartbeat_interval_sec=10,
        buzzer_enabled=True,
        version=1,
    )
    db.add(cfg)
    db.commit()


def run_command_ack_smoke_test():
    print("======================================================================")
    print("   SMOKE TEST: BARRIER CONTROL & HARDWARE ACK LISTENER (MILESTONE 4.4)")
    print("   RBAC PHÂN CẤP RỦI RO, SAFETY LOCK, ASYNC COMMAND & AUDIT LOGS")
    print("======================================================================")

    client = TestClient(app)
    db = SessionLocal()

    # Giả lập MQTT Publish trả về thành công (rc=0) cho các Unit/API Tests khi không chạy Broker nền
    from unittest.mock import MagicMock
    from app.services.mqtt_service import mqtt_service
    orig_publish = mqtt_service.publish
    mock_msg = MagicMock()
    mock_msg.rc = 0
    mqtt_service.publish = MagicMock(return_value=mock_msg)

    try:
        # Dọn dẹp sạch trước khi chạy
        cleanup_test_data(db)
        seed_test_data(db)
        setup_iot_listeners()

        # ----------------------------------------------------
        # 1. ĐĂNG NHẬP 3 VAI TRÒ MẪU LẤY TOKEN
        # ----------------------------------------------------
        print("\n[BƯỚC 1] Đăng nhập xác thực tài khoản ADMIN, OPERATOR, VIEWER...")
        
        admin_res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert admin_res.status_code == 200, f"Login admin lỗi: {admin_res.text}"
        admin_token = admin_res.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        op_res = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
        assert op_res.status_code == 200, f"Login operator lỗi: {op_res.text}"
        op_token = op_res.json()["access_token"]
        op_headers = {"Authorization": f"Bearer {op_token}"}

        vi_res = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
        assert vi_res.status_code == 200, f"Login viewer lỗi: {vi_res.text}"
        vi_token = vi_res.json()["access_token"]
        vi_headers = {"Authorization": f"Bearer {vi_token}"}
        print("   [OK] Lấy Access Token thành công cho cả 3 vai trò.")

        # ----------------------------------------------------
        # 2. KIỂM THỬ PHÂN QUYỀN RBAC (HIERARCHICAL & RISK-BASED)
        # ----------------------------------------------------
        print("\n[BƯỚC 2] Kiểm thử Phân quyền Theo Mức độ Rủi ro (Risk-based RBAC)...")

        # 2.1 VIEWER không được phép truy cập router commands (403 Forbidden)
        res_viewer = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "OPEN", "reason": "Viewer thử mở barrier"},
            headers=vi_headers,
        )
        assert res_viewer.status_code == 403, f"Viewer không bị chặn 403: {res_viewer.status_code}"
        print("   [OK] VIEWER bị chặn 403 Forbidden khi cố gắng phát lệnh điều khiển.")

        # 2.2 OPERATOR gửi lệnh REBOOT (Lệnh kỹ thuật rủi ro cao -> Bị chặn 403)
        res_op_reboot = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "REBOOT", "reason": "Operator thử khởi động lại ESP32"},
            headers=op_headers,
        )
        assert res_op_reboot.status_code == 403, f"Operator gửi REBOOT không bị chặn 403: {res_op_reboot.status_code}"
        assert "Chỉ Quản trị viên (ADMIN)" in res_op_reboot.json()["detail"]
        print("   [OK] OPERATOR bị chặn 403 khi gửi lệnh REBOOT (Chỉ ADMIN được phép).")

        # 2.3 OPERATOR gửi lệnh UPDATE_CONFIG (Lệnh kỹ thuật rủi ro cao -> Bị chặn 403)
        res_op_cfg = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "UPDATE_CONFIG", "reason": "Operator đổi config"},
            headers=op_headers,
        )
        assert res_op_cfg.status_code == 403, f"Operator gửi UPDATE_CONFIG không bị chặn 403: {res_op_cfg.status_code}"
        print("   [OK] OPERATOR bị chặn 403 khi gửi lệnh UPDATE_CONFIG.")

        # 2.4 OPERATOR gửi lệnh OPEN (Vận hành barrier thông thường -> Thành công 201)
        res_op_open = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "OPEN", "reason": "Mở cổng đón xe cứu hỏa"},
            headers=op_headers,
        )
        assert res_op_open.status_code == 201, f"Operator gửi OPEN lỗi: {res_op_open.text}"
        cmd_open_data = res_op_open.json()
        assert cmd_open_data["status"] == "SENT"
        assert cmd_open_data["action"] == "OPEN"
        cmd_id_1 = cmd_open_data["id"]
        print(f"   [OK] OPERATOR phát lệnh OPEN thành công (HTTP 201 Created, ID: {cmd_id_1}).")

        # 2.5 ADMIN gửi lệnh REBOOT -> Thành công 201
        res_admin_reboot = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "REBOOT", "reason": "Bảo trì định kỳ vi điều khiển"},
            headers=admin_headers,
        )
        assert res_admin_reboot.status_code == 201, f"Admin gửi REBOOT lỗi: {res_admin_reboot.text}"
        cmd_reboot_data = res_admin_reboot.json()
        assert cmd_reboot_data["status"] == "SENT"
        assert cmd_reboot_data["action"] == "REBOOT"
        print(f"   [OK] ADMIN phát lệnh kỹ thuật REBOOT thành công (HTTP 201 Created).")

        # ----------------------------------------------------
        # 3. KIỂM THỬ CHẶN AN TOÀN PHẦN CỨNG (HARDWARE SAFETY LOCK)
        # ----------------------------------------------------
        print("\n[BƯỚC 3] Kiểm thử Chặn An toàn Phần cứng (Hardware Safety Lock)...")

        device_obj = db.query(Device).filter(Device.id == TEST_DEVICE_ID).first()

        # 3.1 Chặn khi thiết bị Offline (is_online = False)
        device_obj.is_online = False
        db.commit()

        res_offline = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "OPEN", "reason": "Thử mở khi thiết bị offline"},
            headers=op_headers,
        )
        assert res_offline.status_code == 400, f"Phát lệnh khi offline không bị 400: {res_offline.status_code}"
        assert "ngoại tuyến" in res_offline.json()["detail"].lower()
        print("   [OK] Chặn an toàn 400 Bad Request thành công khi thiết bị Offline.")

        # Khôi phục online
        device_obj.is_online = True
        db.commit()

        # 3.2 Chặn khi thiết bị Suspended (status = SUSPENDED)
        device_obj.status = DeviceStatus.SUSPENDED
        db.commit()

        res_suspended = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "CLOSE", "reason": "Thử đóng khi thiết bị suspended"},
            headers=op_headers,
        )
        assert res_suspended.status_code == 400, f"Phát lệnh khi suspended không bị 400: {res_suspended.status_code}"
        assert "tạm ngưng" in res_suspended.json()["detail"].lower()
        print("   [OK] Chặn an toàn 400 Bad Request thành công khi thiết bị SUSPENDED.")

        # 3.3 Chặn khi thiết bị Decommissioned (status = DECOMMISSIONED)
        device_obj.status = DeviceStatus.DECOMMISSIONED
        db.commit()

        res_decom = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "OPEN", "reason": "Thử mở khi decommissioned"},
            headers=op_headers,
        )
        assert res_decom.status_code == 400, f"Phát lệnh khi decommissioned không bị 400: {res_decom.status_code}"
        assert "ngừng sử dụng" in res_decom.json()["detail"].lower()
        print("   [OK] Chặn an toàn 400 Bad Request thành công khi thiết bị DECOMMISSIONED.")

        # Khôi phục trạng thái ACTIVATED & online
        device_obj.status = DeviceStatus.ACTIVATED
        device_obj.is_online = True
        db.commit()

        # 3.4 Thiết bị không tồn tại -> 404 Not Found
        res_404 = client.post(
            "/api/v1/devices/ESP32_NOT_EXIST_99/commands",
            json={"gate_id": "GATE_IN", "action": "OPEN"},
            headers=op_headers,
        )
        assert res_404.status_code == 404
        print("   [OK] Báo lỗi 404 Not Found chính xác khi thiết bị không tồn tại.")

        # ----------------------------------------------------
        # 4. KIỂM THỬ PHÁT LỆNH & ĐÓN ACK TỪ PHẦN CỨNG ESP32
        # ----------------------------------------------------
        print("\n[BƯỚC 4] Kiểm thử Phát Lệnh Điều khiển & Đón Bản tin Phản hồi ACK...")

        # 4.1 Phát lệnh CLOSE cổng OUT
        res_cmd_close = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_OUT", "action": "CLOSE", "reason": "Đóng cưỡng bức sau khi xe đã qua"},
            headers=op_headers,
        )
        assert res_cmd_close.status_code == 201
        cmd_close_id = res_cmd_close.json()["id"]
        assert res_cmd_close.json()["status"] == "SENT"
        print(f"   [OK] Đã phát lệnh CLOSE tới ESP32 (Mã lệnh: {cmd_close_id}, Trạng thái: SENT).")

        # 4.2 Giả lập ESP32 hoàn tất cơ khí và gửi ACK SUCCESS lên topic MQTT
        ack_success_payload = {
            "token": "dtk_test_cmd_token_123",
            "commandId": cmd_close_id,
            "gate": "GATE_OUT",
            "action": "CLOSE",
            "status": "SUCCESS",
            "reason": "Barrier closed completely (0 deg)",
            "timestamp": "2026-10-17T15:30:00Z",
        }
        ack_topic = f"parking/{TEST_SITE_ID}/{TEST_DEVICE_ID}/ack"
        fake_msg_ack = FakeMqttMessage(ack_topic, ack_success_payload)
        
        # Bắn vào MQTT dispatcher để phân phối tới iot_listener.handle_ack
        mqtt_service._on_message(None, None, fake_msg_ack)

        # Kiểm tra CSDL (commit để reset transaction snapshot)
        db.commit()
        cmd_record = db.query(Command).filter(Command.id == cmd_close_id).first()
        assert cmd_record is not None
        assert cmd_record.status == CommandStatus.ACKED, f"Trạng thái lệnh chưa chuyển thành ACKED: {cmd_record.status}"
        assert cmd_record.ack_status == "SUCCESS"
        assert cmd_record.ack_reason == "Barrier closed completely (0 deg)"
        assert cmd_record.acked_at is not None
        print(f"   [OK] IoT Listener đã tiếp nhận ACK SUCCESS: Lệnh '{cmd_close_id}' đã chuyển sang ACKED.")

        # 4.3 Phát lệnh thứ hai để test kịch bản ACK FAILED (Ví dụ có vật cản kẹt servo)
        res_cmd_fail = client.post(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands",
            json={"gate_id": "GATE_IN", "action": "CLOSE", "reason": "Đóng cổng IN"},
            headers=op_headers,
        )
        assert res_cmd_fail.status_code == 201
        cmd_fail_id = res_cmd_fail.json()["id"]

        ack_failed_payload = {
            "token": "dtk_test_cmd_token_123",
            "commandId": cmd_fail_id,
            "status": "FAILED",
            "reason": "Ultrasonic detected vehicle still underneath barrier",
        }
        fake_msg_fail = FakeMqttMessage(ack_topic, ack_failed_payload)
        mqtt_service._on_message(None, None, fake_msg_fail)

        db.commit()
        cmd_fail_record = db.query(Command).filter(Command.id == cmd_fail_id).first()
        assert cmd_fail_record.status == CommandStatus.FAILED, f"Trạng thái lệnh chưa chuyển thành FAILED: {cmd_fail_record.status}"
        assert cmd_fail_record.ack_status == "FAILED"
        assert "still underneath" in cmd_fail_record.ack_reason
        print(f"   [OK] IoT Listener đã tiếp nhận ACK FAILED: Lệnh '{cmd_fail_id}' đã chuyển sang FAILED an toàn.")

        # ----------------------------------------------------
        # 5. KIỂM THỬ API TRUY VẤN LỊCH SỬ VÀ CHI TIẾT LỆNH
        # ----------------------------------------------------
        print("\n[BƯỚC 5] Kiểm thử API Truy vấn Lịch sử & Chi tiết Lệnh Điều khiển...")

        # 5.1 Tra cứu danh sách lệnh của thiết bị
        res_list = client.get(f"/api/v1/devices/{TEST_DEVICE_ID}/commands", headers=op_headers)
        assert res_list.status_code == 200
        cmd_list = res_list.json()
        assert len(cmd_list) >= 3, f"Số lượng lệnh tìm thấy ít hơn dự kiến: {len(cmd_list)}"
        print(f"   [OK] Lấy danh sách lệnh điều khiển của thiết bị thành công ({len(cmd_list)} bản ghi).")

        # 5.2 Lọc theo action
        res_filter_action = client.get(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands?action=CLOSE",
            headers=op_headers,
        )
        assert res_filter_action.status_code == 200
        filtered_actions = res_filter_action.json()
        assert all(c["action"] == "CLOSE" for c in filtered_actions)
        print(f"   [OK] Bộ lọc query parameter 'action=CLOSE' hoạt động chính xác.")

        # 5.3 Lọc theo status
        res_filter_status = client.get(
            f"/api/v1/devices/{TEST_DEVICE_ID}/commands?status=ACKED",
            headers=op_headers,
        )
        assert res_filter_status.status_code == 200
        filtered_statuses = res_filter_status.json()
        assert all(c["status"] == "ACKED" for c in filtered_statuses)
        print(f"   [OK] Bộ lọc query parameter 'status=ACKED' hoạt động chính xác.")

        # 5.4 Tra cứu chi tiết một lệnh cụ thể
        res_detail = client.get(f"/api/v1/commands/{cmd_close_id}", headers=op_headers)
        assert res_detail.status_code == 200
        detail_data = res_detail.json()
        assert detail_data["id"] == cmd_close_id
        assert detail_data["status"] == "ACKED"
        assert detail_data["ack_status"] == "SUCCESS"
        print(f"   [OK] Tra cứu chi tiết lệnh '{cmd_close_id}' qua GET /api/v1/commands/{cmd_close_id} chính xác.")

        # 5.5 Tra cứu lệnh không tồn tại -> 404 Not Found
        res_cmd_404 = client.get("/api/v1/commands/cmd_fake_not_exist", headers=op_headers)
        assert res_cmd_404.status_code == 404
        print("   [OK] Tra cứu mã lệnh không tồn tại trả về 404 Not Found chính xác.")

        # ----------------------------------------------------
        # 6. KIỂM THỬ ĐỐI SOÁT AUDIT LOGS
        # ----------------------------------------------------
        print("\n[BƯỚC 6] Đối soát Vết Kiểm toán Bảo mật (Audit Logs)...")

        audits = db.query(AuditLog).filter(
            AuditLog.resource_type == "devices",
            AuditLog.resource_id == TEST_DEVICE_ID,
        ).all()
        assert len(audits) >= 3, f"Số lượng audit log không đủ: {len(audits)}"
        action_names = [a.action for a in audits]
        assert "BARRIER_OPEN" in action_names
        assert "BARRIER_CLOSE" in action_names
        assert "BARRIER_REBOOT" in action_names
        print(f"   [OK] Audit Logs đã ghi nhận đầy đủ {len(audits)} hành động điều khiển: {set(action_names)}.")

        # ----------------------------------------------------
        # TỔNG KẾT
        # ----------------------------------------------------
        print("\n======================================================================")
        print("   >>> TẤT CẢ 6 BƯỚC KIỂM THỬ MILESTONE 4.4 ĐÃ VƯỢT QUA 100%! <<<")
        print("======================================================================")

    finally:
        mqtt_service.publish = orig_publish
        print("\n[DỌN DẸP] Tiến hành làm sạch dữ liệu kiểm thử trong CSDL...")
        cleanup_test_data(db)
        db.close()
        print("   [OK] Đã hoàn tất dọn dẹp dữ liệu kiểm thử.")


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_command_ack_smoke_test()
