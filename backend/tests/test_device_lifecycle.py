import os
import sys

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
from datetime import datetime

from app.database import SessionLocal
from app.main import app
from app.models.device import Device, DeviceConfig
from app.models.audit import AuditLog
from app.models.enums import DeviceStatus


def cleanup_device_test_data(db, test_device_ids: list[str]):
    """Dọn dẹp thiết bị test và audit logs liên quan để bảo đảm CSDL luôn nhất quán."""
    try:
        if test_device_ids:
            # Xóa audit logs liên quan
            db.query(AuditLog).filter(
                AuditLog.resource_type.in_(["devices", "device_configs"]),
                AuditLog.resource_id.in_(test_device_ids),
            ).delete(synchronize_session=False)

            # Xóa device_configs (cascade hoặc trực tiếp)
            db.query(DeviceConfig).filter(DeviceConfig.device_id.in_(test_device_ids)).delete(synchronize_session=False)

            # Xóa devices
            db.query(Device).filter(Device.id.in_(test_device_ids)).delete(synchronize_session=False)

            db.commit()
    except Exception as e:
        db.rollback()
        print(f"   [CẢNH BÁO] Không thể dọn dẹp dữ liệu test: {e}")


def run_device_lifecycle_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: IOT DEVICE LIFECYCLE & REMOTE CONFIG (MILESTONE 4.1)")
    print("   10 BƯỚC VÒNG ĐỜI THIẾT BỊ, TOKEN AUTH & RBAC")
    print("==================================================")

    client = TestClient(app)
    db = SessionLocal()

    # Giả lập MQTT Publish trả về thành công (rc=0) cho các Unit/API Tests khi không chạy Broker nền
    from unittest.mock import MagicMock
    from app.services.mqtt_service import mqtt_service
    orig_publish = mqtt_service.publish
    mock_msg = MagicMock()
    mock_msg.rc = 0
    mqtt_service.publish = MagicMock(return_value=mock_msg)

    test_device_id = "ESP32_TEST_GATE_99"
    created_devices = [test_device_id]

    try:
        # 0. Dọn dẹp trước nếu còn sót lại từ lần chạy trước
        cleanup_device_test_data(db, created_devices)

        # ----------------------------------------------------
        # 1. ĐĂNG NHẬP CẤP BEARER TOKEN
        # ----------------------------------------------------
        print("\n[BƯỚC 1] Đăng nhập 3 tài khoản Admin, Operator, Viewer lấy Token...")
        admin_res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert admin_res.status_code == 200
        admin_headers = {"Authorization": f"Bearer {admin_res.json()['access_token']}"}

        op_res = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
        assert op_res.status_code == 200
        op_headers = {"Authorization": f"Bearer {op_res.json()['access_token']}"}

        vi_res = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
        assert vi_res.status_code == 200
        vi_headers = {"Authorization": f"Bearer {vi_res.json()['access_token']}"}
        print("   [OK] Lấy Bearer Token thành công cho Admin, Operator, Viewer.")

        # ----------------------------------------------------
        # 2. KIỂM THỬ PHÂN QUYỀN RBAC TRÊN ROUTER DEVICES
        # ----------------------------------------------------
        print("\n[BƯỚC 2] Kiểm thử Phân quyền RBAC (VIEWER bị chặn 403, OPERATOR chỉ GET)...")

        # 2.1 VIEWER không thể xem danh sách thiết bị (403)
        res_vi = client.get("/api/v1/devices", headers=vi_headers)
        assert res_vi.status_code == 403, "Viewer phải bị chặn khi xem devices"
        print("   [OK] Viewer bị chặn chính xác với mã lỗi 403 Forbidden.")

        # 2.2 OPERATOR không có quyền tạo thiết bị mới (403)
        res_op_create = client.post(
            "/api/v1/devices",
            headers=op_headers,
            json={
                "id": test_device_id,
                "name": "Gate Controller Test",
                "site_id": "PTIT_ZONE_1",
            },
        )
        assert res_op_create.status_code == 403, "Operator không được phép tạo thiết bị"
        print("   [OK] Operator bị chặn khi cố gắng đăng ký thiết bị mới (403 Forbidden).")

        # ----------------------------------------------------
        # 3. VÒNG ĐỜI BƯỚC 1 & 2: ONBOARDING & CẤP TOKEN
        # ----------------------------------------------------
        print("\n[BƯỚC 3] Vòng đời Bước 1 & 2: ADMIN Onboarding Thiết bị & Tự động Cấp Token dtk_...")
        res_create = client.post(
            "/api/v1/devices",
            headers=admin_headers,
            json={
                "id": test_device_id,
                "name": "ESP32 Cổng vào & Cảm biến Siêu âm Phòng Lab",
                "site_id": "PTIT_ZONE_1",
                "ip_address": "192.168.1.199",
                "hw_version": "ESP32-WROOM-32D",
                "fw_version": "1.0.0",
            },
        )
        assert res_create.status_code == 201, f"Đăng ký thiết bị thất bại: {res_create.text}"
        dev_data = res_create.json()

        assert dev_data["id"] == test_device_id
        assert dev_data["status"] == DeviceStatus.REGISTERED.value
        assert dev_data["is_online"] is False
        assert dev_data["auth_token"].startswith("dtk_")
        assert len(dev_data["auth_token"]) >= 28

        # Kiểm tra config mặc định được tạo kèm
        config_data = dev_data.get("config")
        assert config_data is not None, "Thiết bị mới phải có cấu hình mặc định đính kèm"
        assert config_data["distance_threshold_cm"] == 15.00
        assert config_data["servo_open_time_sec"] == 10
        assert config_data["heartbeat_interval_sec"] == 10
        assert config_data["buzzer_enabled"] is True
        assert config_data["version"] == 1
        print(f"   [OK] Onboarding thành công! Auth Token: {dev_data['auth_token'][:12]}... | Config Version: {config_data['version']}")

        # Kiểm tra tính duy nhất (Không cho phép trùng lặp ID)
        res_duplicate = client.post(
            "/api/v1/devices",
            headers=admin_headers,
            json={
                "id": test_device_id,
                "name": "Thiết bị trùng ID",
            },
        )
        assert res_duplicate.status_code == 400, "Không được phép đăng ký trùng ID thiết bị"
        print("   [OK] Chặn trùng lặp ID thiết bị thành công (400 Bad Request).")

        # ----------------------------------------------------
        # 4. VÒNG ĐỜI BƯỚC 3: XÁC THỰC PHÂN VÙNG BÃI ĐỖ (SITE_ID)
        # ----------------------------------------------------
        print("\n[BƯỚC 4] Vòng đời Bước 3: Xác thực Phân vùng bãi đỗ (site_id = PTIT_ZONE_1)...")
        res_detail = client.get(f"/api/v1/devices/{test_device_id}", headers=op_headers)
        assert res_detail.status_code == 200
        assert res_detail.json()["site_id"] == "PTIT_ZONE_1"
        print("   [OK] Thiết bị đã được gán chính xác vào phân vùng PTIT_ZONE_1.")

        # ----------------------------------------------------
        # 5. VÒNG ĐỜI BƯỚC 4: KÍCH HOẠT THIẾT BỊ (ACTIVATED)
        # ----------------------------------------------------
        print("\n[BƯỚC 5] Vòng đời Bước 4: Kích hoạt thiết bị đưa vào vận hành (ACTIVATED)...")
        res_activate = client.patch(
            f"/api/v1/devices/{test_device_id}/status",
            headers=admin_headers,
            json={
                "status": DeviceStatus.ACTIVATED.value,
                "reason": "Hoàn tất nạp firmware và lắp đặt cơ khí barrier",
            },
        )
        assert res_activate.status_code == 200
        assert res_activate.json()["status"] == DeviceStatus.ACTIVATED.value
        print("   [OK] Thiết bị đã chuyển trạng thái thành công sang ACTIVATED.")

        # ----------------------------------------------------
        # 6. VÒNG ĐỜI BƯỚC 5: CẬP NHẬT CẤU HÌNH THAM SỐ TỪ XA
        # ----------------------------------------------------
        print("\n[BƯỚC 6] Vòng đời Bước 5: Cập nhật tham số cấu hình từ xa (Remote Config)...")
        res_config = client.patch(
            f"/api/v1/devices/{test_device_id}/config",
            headers=admin_headers,
            json={
                "distance_threshold_cm": 25.5,
                "servo_open_time_sec": 15,
                "heartbeat_interval_sec": 15,
                "buzzer_enabled": False,
            },
        )
        assert res_config.status_code == 200
        updated_cfg = res_config.json()["config"]
        assert updated_cfg["distance_threshold_cm"] == 25.5
        assert updated_cfg["servo_open_time_sec"] == 15
        assert updated_cfg["heartbeat_interval_sec"] == 15
        assert updated_cfg["buzzer_enabled"] is False
        assert updated_cfg["version"] == 2
        print(f"   [OK] Cập nhật tham số từ xa thành công! Phiên bản cấu hình tăng lên: v{updated_cfg['version']}")

        # ----------------------------------------------------
        # 7. VÒNG ĐỜI BƯỚC 6 & 7: GIẢ LẬP HEARTBEAT & FIRMWARE CHECK
        # ----------------------------------------------------
        print("\n[BƯỚC 7] Vòng đời Bước 6 & 7: Kiểm tra theo dõi nhịp tim (Heartbeat) & Firmware...")
        # Cập nhật trực tiếp thời điểm heartbeat và online vào CSDL (mô phỏng IoT Listener Milestone 4.3)
        dev_orm = db.query(Device).filter(Device.id == test_device_id).first()
        dev_orm.is_online = True
        dev_orm.last_heartbeat_at = datetime.now()
        dev_orm.fw_version = "1.0.1"
        db.commit()

        # Gọi GET để xác thực dữ liệu hiển thị qua API
        res_hb_check = client.get(f"/api/v1/devices/{test_device_id}", headers=op_headers)
        assert res_hb_check.status_code == 200
        dev_hb_data = res_hb_check.json()
        assert dev_hb_data["is_online"] is True
        assert dev_hb_data["fw_version"] == "1.0.1"
        assert dev_hb_data["last_heartbeat_at"] is not None
        print(f"   [OK] Giám sát thiết bị: is_online = True | fw_version = {dev_hb_data['fw_version']} | last_heartbeat = {dev_hb_data['last_heartbeat_at']}")

        # ----------------------------------------------------
        # 8. VÒNG ĐỜI BƯỚC 8: BẢO TRÌ KỸ THUẬT (MAINTENANCE)
        # ----------------------------------------------------
        print("\n[BƯỚC 8] Vòng đời Bước 8: Đưa thiết bị vào bảo trì kỹ thuật (MAINTENANCE)...")
        res_maint = client.patch(
            f"/api/v1/devices/{test_device_id}/status",
            headers=admin_headers,
            json={
                "status": DeviceStatus.MAINTENANCE.value,
                "reason": "Kiểm tra định kỳ servo và hiệu chuẩn cảm biến siêu âm",
            },
        )
        assert res_maint.status_code == 200
        assert res_maint.json()["status"] == DeviceStatus.MAINTENANCE.value
        assert res_maint.json()["is_online"] is False
        print("   [OK] Thiết bị đã chuyển sang MAINTENANCE và tự động offline.")

        # ----------------------------------------------------
        # 9. VÒNG ĐỜI BƯỚC 9: TẠM KHÓA KHẨN CẤP (SUSPENDED)
        # ----------------------------------------------------
        print("\n[BƯỚC 9] Vòng đời Bước 9: Tạm ngưng hoạt động khẩn cấp (SUSPENDED)...")
        res_suspend = client.patch(
            f"/api/v1/devices/{test_device_id}/status",
            headers=admin_headers,
            json={
                "status": DeviceStatus.SUSPENDED.value,
                "reason": "Cảnh báo chạm chập mạch nguồn barrier cổng vào",
            },
        )
        assert res_suspend.status_code == 200
        assert res_suspend.json()["status"] == DeviceStatus.SUSPENDED.value
        print("   [OK] Thiết bị đã chuyển trạng thái thành công sang SUSPENDED.")

        # ----------------------------------------------------
        # 10. VÒNG ĐỜI BƯỚC 10: NGỪNG SỬ DỤNG VĨNH VIỄN (DECOMMISSIONED)
        # ----------------------------------------------------
        print("\n[BƯỚC 10] Vòng đời Bước 10: Khai tử thiết bị vĩnh viễn (DECOMMISSIONED)...")
        res_decom = client.patch(
            f"/api/v1/devices/{test_device_id}/status",
            headers=admin_headers,
            json={
                "status": DeviceStatus.DECOMMISSIONED.value,
                "reason": "Hỏng bo mạch chính không thể sửa chữa, tiến hành thanh lý",
            },
        )
        assert res_decom.status_code == 200
        assert res_decom.json()["status"] == DeviceStatus.DECOMMISSIONED.value
        print("   [OK] Thiết bị đã chuyển sang trạng thái DECOMMISSIONED.")

        # 10.1 KIỂM TRA QUY TẮC BẤT BIẾN: KHÓA MỘT CHIỀU VĨNH VIỄN
        print("\n[BƯỚC 10.1] Kiểm tra Quy tắc Bất biến: Chặn kích hoạt lại thiết bị DECOMMISSIONED...")
        res_reactivate = client.patch(
            f"/api/v1/devices/{test_device_id}/status",
            headers=admin_headers,
            json={
                "status": DeviceStatus.ACTIVATED.value,
                "reason": "Cố gắng kích hoạt lại thiết bị đã khai tử",
            },
        )
        assert res_reactivate.status_code == 400, "Thiết bị DECOMMISSIONED không được phép đổi trạng thái"
        print(f"   [OK] Hệ thống từ chối chính xác (400 Bad Request): {res_reactivate.json()['detail']}")

        # ----------------------------------------------------
        # 11. KIỂM TRA LỊCH SỬ KIỂM TOÁN (AUDIT LOGS)
        # ----------------------------------------------------
        print("\n[BƯỚC 11] Kiểm tra Nhật ký kiểm toán (Audit Logs) sinh ra trong vòng đời...")
        audit_records = (
            db.query(AuditLog)
            .filter(AuditLog.resource_id == test_device_id)
            .order_by(AuditLog.id.asc())
            .all()
        )
        print(f"   [INFO] Đã ghi nhận {len(audit_records)} bản ghi Audit Log cho thiết bị {test_device_id}:")
        for record in audit_records:
            print(f"      - Action: {record.action:<22} | Details: {record.details}")

        assert len(audit_records) >= 5, "Phải ghi nhận đầy đủ các thao tác Onboarding, Sửa Config và Đổi Trạng Thái"
        print("   [OK] Audit Logs ghi nhận đầy đủ, minh bạch toàn bộ vòng đời thiết bị.")

        # ----------------------------------------------------
        # 12. KIỂM THỬ LỌC VÀ TÌM KIẾM THIẾT BỊ (GET /devices)
        # ----------------------------------------------------
        print("\n[BƯỚC 12] Kiểm thử Tra cứu & Lọc thiết bị...")
        res_filter_status = client.get("/api/v1/devices?status=DECOMMISSIONED", headers=op_headers)
        assert res_filter_status.status_code == 200
        assert any(d["id"] == test_device_id for d in res_filter_status.json())

        res_search = client.get(f"/api/v1/devices?search={test_device_id}", headers=op_headers)
        assert res_search.status_code == 200
        assert len(res_search.json()) >= 1
        print("   [OK] Lọc theo trạng thái và tìm kiếm thiết bị hoạt động chính xác.")

        print("\n==================================================")
        print("   HOÀN TẤT KIỂM THỬ: 100% TEST LIFECYCLE ĐẠT CHUẨN!")
        print("==================================================")

    finally:
        mqtt_service.publish = orig_publish
        cleanup_device_test_data(db, created_devices)
        db.close()


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_device_lifecycle_smoke_test()
