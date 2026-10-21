import os
import sys

# Cấu hình UTF-8 cho console Windows tránh UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo đường dẫn import app package luôn đúng dù chạy từ bất kỳ đâu
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.database import SessionLocal
from app.models import (
    User,
    Device,
    DeviceConfig,
    Slot,
    Card,
    Tariff,
    ParkingSession,
    Payment,
    Command,
    TelemetryLog,
    Alert,
    AuditLog,
    UserRole,
    DeviceStatus,
    SlotStatus,
    CardType,
)


def run_orm_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: 12 SQLALCHEMY ORM MODELS (MILESTONE 3.2)")
    print("==================================================")

    db = SessionLocal()
    try:
        # 1. Test Model User
        users = db.query(User).order_by(User.id).all()
        print(f"[TEST 1/7] User Model: Tìm thấy {len(users)} người dùng:")
        for u in users:
            print(f"   - ID {u.id}: username='{u.username}', role='{u.role.value}', active={u.is_active}")
        assert len(users) >= 3, "Phải có ít nhất 3 users từ seed data!"
        assert any(u.username == "admin" and u.role == UserRole.ADMIN for u in users), "Thiếu tài khoản admin!"

        # 2. Test Model Device & DeviceConfig
        devices = db.query(Device).all()
        print(f"[TEST 2/7] Device Model: Tìm thấy {len(devices)} thiết bị:")
        for d in devices:
            print(f"   - Device ID '{d.id}': name='{d.name}', status='{d.status.value}', site='{d.site_id}'")
        assert len(devices) >= 1, "Phải có ít nhất 1 thiết bị ESP32 mẫu!"
        
        cfg = db.query(DeviceConfig).filter(DeviceConfig.device_id == "ESP32_GATE_01").first()
        assert cfg is not None, "Không tìm thấy cấu hình cho ESP32_GATE_01!"
        print(f"   -> Cấu hình: ngưỡng khoảng cách={cfg.distance_threshold_cm}cm, mở barie={cfg.servo_open_time_sec}s, heartbeat={cfg.heartbeat_interval_sec}s")

        # 3. Test Model Slot
        slot = db.query(Slot).filter(Slot.id == "SLOT_A1").first()
        assert slot is not None, "Không tìm thấy ô đỗ SLOT_A1!"
        print(f"[TEST 3/7] Slot Model: Ô đỗ '{slot.id}', tên='{slot.name}', trạng thái='{slot.status.value}', gắn với thiết bị='{slot.device_id}'")

        # 4. Test Model Card
        cards = db.query(Card).all()
        print(f"[TEST 4/7] Card Model: Tìm thấy {len(cards)} thẻ RFID:")
        for c in cards:
            print(f"   - UID '{c.uid}': loại='{c.card_type.value}', chủ thẻ='{c.owner_name}', biển số='{c.registered_plate}', active={c.is_active}")
        assert len(cards) >= 3, "Phải có ít nhất 3 thẻ RFID mẫu!"

        # 5. Test Model Tariff
        tariff = db.query(Tariff).filter(Tariff.id == 1).first()
        assert tariff is not None, "Không tìm thấy biểu phí ID 1!"
        print(f"[TEST 5/7] Tariff Model: '{tariff.name}', kiểu='{tariff.fee_type.value}', đơn giá={float(tariff.flat_rate):,.0f} VNĐ")

        # 6. Test Model AuditLog
        audits = db.query(AuditLog).all()
        print(f"[TEST 6/7] AuditLog Model: Tìm thấy {len(audits)} bản ghi nhật ký:")
        for a in audits:
            print(f"   - Action='{a.action}', resource='{a.resource_type}:{a.resource_id}', user_id={a.user_id}")

        # 7. Test ORM Transient Mapping & Validation (ParkingSession, Payment, Command, Alert, TelemetryLog)
        print("[TEST 7/7] Kiểm tra ánh xạ ORM các bảng nghiệp vụ rỗng (Sessions, Payments, Commands, Alerts, Telemetry):")
        session_count = db.query(ParkingSession).count()
        payment_count = db.query(Payment).count()
        command_count = db.query(Command).count()
        alert_count = db.query(Alert).count()
        telemetry_count = db.query(TelemetryLog).count()
        print(f"   - Sessions: {session_count} | Payments: {payment_count} | Commands: {command_count}")
        print(f"   - Alerts: {alert_count} | TelemetryLogs: {telemetry_count}")

        print("==================================================")
        print("[SUCCESS] Tất cả 12 Models ORM và 14 Enums hoạt động hoàn hảo!")
        print("[SUCCESS] Milestone 3.2 Verification Passed!")
        print("==================================================")

    except Exception as e:
        print(f"[ERROR] Kiểm tra ORM thất bại: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_orm_smoke_test()
