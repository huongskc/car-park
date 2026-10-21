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

from app.database import SessionLocal
from app.main import app
from app.models.slot import Slot
from app.models.session import ParkingSession
from app.models.payment import Payment
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.enums import SlotStatus, SessionStatus, AlertType, AlertSeverity, AlertStatus


def cleanup_test_data(db, session_ids: list[int], alert_ids: list[int]):
    """Dọn dẹp các bản ghi test để bảo đảm tính độc lập của CSDL."""
    try:
        if session_ids:
            # Xóa payments liên quan
            db.query(Payment).filter(Payment.session_id.in_(session_ids)).delete(synchronize_session=False)
            # Xóa audit logs liên quan
            db.query(AuditLog).filter(
                AuditLog.resource_type == "parking_sessions",
                AuditLog.resource_id.in_([str(sid) for sid in session_ids]),
            ).delete(synchronize_session=False)
            # Xóa sessions
            db.query(ParkingSession).filter(ParkingSession.id.in_(session_ids)).delete(synchronize_session=False)

        if alert_ids:
            db.query(Alert).filter(Alert.id.in_(alert_ids)).delete(synchronize_session=False)

        # Đảm bảo ô đỗ SLOT_A1 luôn trở về trạng thái EMPTY
        slot_a1 = db.query(Slot).filter(Slot.id == "SLOT_A1").first()
        if slot_a1:
            slot_a1.status = SlotStatus.EMPTY

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"   [CẢNH BÁO] Không thể dọn dẹp dữ liệu test: {e}")


def run_operational_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: OPERATIONAL ROUTERS & MAIN INTEGRATION (MILESTONE 3.5)")
    print("   SESSIONS, CHECKOUT, RESOLVE-PLATE, ALERTS, AUDITS & OPENAPI")
    print("==================================================")

    client = TestClient(app)
    db = SessionLocal()

    created_session_ids = []
    created_alert_ids = []

    try:
        # ----------------------------------------------------
        # 1. KIỂM TRA MAIN APP & OPENAPI SPEC
        # ----------------------------------------------------
        print("\n[BƯỚC 1] Kiểm tra Endpoint Gốc, Health Check & OpenAPI Schema...")
        
        res_root = client.get("/")
        assert res_root.status_code == 200, f"Root endpoint lỗi: {res_root.text}"
        root_data = res_root.json()
        assert root_data["status"] == "online"
        print(f"   [OK] Root endpoint phản hồi tốt: {root_data['system']} v{root_data['version']}")

        res_health = client.get("/health")
        assert res_health.status_code == 200 and res_health.json()["status"] == "healthy"
        print("   [OK] Health check endpoint trả về 'healthy'.")

        res_openapi = client.get("/openapi.json")
        assert res_openapi.status_code == 200, "OpenAPI schema không phản hồi 200."
        openapi_schema = res_openapi.json()
        paths = openapi_schema.get("paths", {})
        
        required_prefixes = ["/api/v1/auth", "/api/v1/users", "/api/v1/cards", "/api/v1/slots", "/api/v1/sessions", "/api/v1/alerts", "/api/v1/audit-logs"]
        for prefix in required_prefixes:
            has_prefix = any(p.startswith(prefix) for p in paths.keys())
            assert has_prefix, f"Thiếu router prefix '{prefix}' trong OpenAPI schema!"
        print(f"   [OK] OpenAPI Schema tích hợp đầy đủ 7 router ({len(paths)} endpoints).")

        # ----------------------------------------------------
        # 2. ĐĂNG NHẬP 3 VAI TRÒ MẪU LẤY TOKEN
        # ----------------------------------------------------
        print("\n[BƯỚC 2] Đăng nhập xác thực 3 vai trò mẫu...")
        
        admin_res = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert admin_res.status_code == 200
        admin_token = admin_res.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        op_res = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
        assert op_res.status_code == 200
        op_token = op_res.json()["access_token"]
        op_headers = {"Authorization": f"Bearer {op_token}"}

        vi_res = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
        assert vi_res.status_code == 200
        vi_token = vi_res.json()["access_token"]
        vi_headers = {"Authorization": f"Bearer {vi_token}"}
        print("   [OK] Cấp phát Bearer Token cho Admin, Operator và Viewer thành công.")

        # ----------------------------------------------------
        # 3. KIỂM THỬ ROUTER QUẢN LÝ PHIÊN XE (/sessions)
        # ----------------------------------------------------
        print("\n[BƯỚC 3] Kiểm thử Router Quản lý Phiên gửi xe (/api/v1/sessions)...")

        # 3.1 Phân quyền: VIEWER bị chặn 403
        res = client.get("/api/v1/sessions", headers=vi_headers)
        assert res.status_code == 403, "Viewer không được phép xem sessions (403 Forbidden)."
        print("   [OK] Viewer bị chặn chính xác khi truy cập sessions (403 Forbidden).")

        # 3.2 OPERATOR xem danh sách phiên xe
        res_op_sessions = client.get("/api/v1/sessions", headers=op_headers)
        assert res_op_sessions.status_code == 200
        print("   [OK] Operator xem danh sách phiên xe thành công (200 OK).")

        # 3.3 Khởi tạo phiên gửi xe mới (Xe vào)
        create_payload = {
            "card_uid": "A1B2C3D4",  # Seed visitor card
            "slot_id": "SLOT_A1",
            "entry_plate": "30A-999.88",
            "entry_image_url": "/images/in_test_01.jpg"
        }
        res_create = client.post("/api/v1/sessions", json=create_payload, headers=op_headers)
        assert res_create.status_code == 201, f"Tạo phiên xe thất bại: {res_create.text}"
        session_data = res_create.json()
        session_id = session_data["id"]
        created_session_ids.append(session_id)
        assert session_data["status"] == "ACTIVE"
        assert session_data["is_paid"] is False
        print(f"   [OK] Tạo phiên gửi xe mới #{session_data['session_code']} (ID: {session_id}).")

        # Kiểm tra ô đỗ đã tự động chuyển sang OCCUPIED
        slot_check = db.query(Slot).filter(Slot.id == "SLOT_A1").first()
        db.refresh(slot_check)
        assert slot_check.status == SlotStatus.OCCUPIED, "Ô đỗ phải tự động chuyển sang OCCUPIED."
        print("   [OK] Trạng thái ô đỗ 'SLOT_A1' tự động cập nhật sang OCCUPIED.")

        # 3.4 Kiểm tra chống quẹt thẻ đúp
        res_dup = client.post("/api/v1/sessions", json=create_payload, headers=op_headers)
        assert res_dup.status_code == 400, "Phải chặn quẹt thẻ đúp với mã 400 Bad Request."
        print("   [OK] Chặn thành công quẹt thẻ đúp (400 Bad Request).")

        # 3.5 Xem chi tiết phiên xe
        res_detail = client.get(f"/api/v1/sessions/{session_id}", headers=op_headers)
        assert res_detail.status_code == 200 and res_detail.json()["id"] == session_id
        print("   [OK] Xem chi tiết phiên xe thành công.")

        # 3.6 Giả lập ngoại lệ lệch biển số & Duyệt ngoại lệ (resolve-plate)
        # Gửi checkout với biển số ra bị lệch để kích hoạt cảnh báo và chuyển PENDING_REVIEW
        res_mismatch = client.post(
            f"/api/v1/sessions/{session_id}/checkout",
            json={"payment_method": "CASH", "exit_plate": "29A-999.99"},
            headers=op_headers,
        )
        assert res_mismatch.status_code == 409, "Phải kích hoạt 409 Conflict khi lệch biển số."
        print("   [OK] Phát hiện lệch biển số (409 Conflict), phiên tự động chuyển PENDING_REVIEW.")

        # Thu gom Alert PLATE_MISMATCH tự động sinh để bảo đảm dọn dẹp sạch sẽ sau test
        db.commit()
        auto_alert = (
            db.query(Alert)
            .filter(
                Alert.alert_type == AlertType.PLATE_MISMATCH,
                Alert.message.contains(session_data["session_code"]),
            )
            .order_by(Alert.id.desc())
            .first()
        )
        if auto_alert:
            created_alert_ids.append(auto_alert.id)

        resolve_payload = {
            "approved": True,
            "note": "Biển số OCR bị lóa đèn, nhân viên đã đối chiếu vé xe và căn cước"
        }
        res_resolve = client.patch(f"/api/v1/sessions/{session_id}/resolve-plate", json=resolve_payload, headers=op_headers)
        assert res_resolve.status_code == 200, f"Duyệt lệch biển thất bại: {res_resolve.text}"
        resolved_data = res_resolve.json()
        assert resolved_data["status"] == "ACTIVE"
        assert resolved_data["reviewed_by"] is not None
        print("   [OK] Duyệt ngoại lệ lệch biển số thành công, phiên xe trở về trạng thái ACTIVE.")

        # Kiểm tra Audit Log được sinh tự động
        db.commit()
        audit_entry = db.query(AuditLog).filter(
            AuditLog.resource_type == "parking_sessions",
            AuditLog.resource_id == str(session_id),
            AuditLog.action == "RESOLVE_PLATE_MISMATCH"
        ).first()
        assert audit_entry is not None, "Audit Log phải tự động được ghi nhận sau khi duyệt lệch biển số!"
        print(f"   [OK] Audit Log được ghi nhận tự động vào bảng audit_logs (ID: {audit_entry.id}).")

        # 3.7 Thu phí & Kết thúc phiên gửi xe (checkout) - Bắt buộc cung cấp exit_plate đã duyệt
        checkout_payload = {"payment_method": "CASH", "exit_plate": "29A-999.99"}
        res_checkout = client.post(f"/api/v1/sessions/{session_id}/checkout", json=checkout_payload, headers=op_headers)
        assert res_checkout.status_code == 200, f"Checkout thất bại: {res_checkout.text}"
        checkout_data = res_checkout.json()
        assert checkout_data["status"] == "COMPLETED"
        assert checkout_data["is_paid"] is True
        assert checkout_data["exit_time"] is not None
        print("   [OK] Checkout thành công: status=COMPLETED, is_paid=True, có mốc exit_time.")

        # Commit session để làm mới transaction snapshot trong MySQL (REPEATABLE READ)
        db.commit()

        # Kiểm tra bản ghi Payment
        payment_record = db.query(Payment).filter(Payment.session_id == session_id).first()
        assert payment_record is not None, "Phải tạo bản ghi trong bảng payments."
        assert float(payment_record.amount) == 20000.00
        print(f"   [OK] Bản ghi Payment tự động được tạo với số tiền {payment_record.amount} VNĐ.")

        # Kiểm tra ô đỗ đã được giải phóng về EMPTY
        slot_check = db.query(Slot).filter(Slot.id == "SLOT_A1").first()
        assert slot_check.status == SlotStatus.EMPTY, "Ô đỗ phải tự động được giải phóng về EMPTY sau khi checkout."
        print("   [OK] Ô đỗ 'SLOT_A1' tự động được giải phóng về trạng thái EMPTY.")

        # 3.8 Chặn thanh toán lại lần 2
        res_recheckout = client.post(f"/api/v1/sessions/{session_id}/checkout", json=checkout_payload, headers=op_headers)
        assert res_recheckout.status_code == 400
        print("   [OK] Chặn thành công thanh toán đúp phiên đã hoàn tất (400 Bad Request).")

        # ----------------------------------------------------
        # 4. KIỂM THỬ ROUTER CẢNH BÁO SỰ CỐ (/alerts)
        # ----------------------------------------------------
        print("\n[BƯỚC 4] Kiểm thử Router Quản lý Cảnh báo sự cố (/api/v1/alerts)...")

        # 4.1 VIEWER bị chặn 403
        res_vi_alert = client.get("/api/v1/alerts", headers=vi_headers)
        assert res_vi_alert.status_code == 403
        print("   [OK] Viewer bị chặn truy cập alerts (403 Forbidden).")

        # 4.2 Tạo cảnh báo mẫu trong CSDL
        test_alert = Alert(
            alert_type=AlertType.PLATE_MISMATCH,
            severity=AlertSeverity.WARNING,
            message="Xe ra biển số 30A-999.88 không khớp lúc vào 30A-111.22",
            session_id=session_id,
            status=AlertStatus.NEW,
        )
        db.add(test_alert)
        db.commit()
        db.refresh(test_alert)
        created_alert_ids.append(test_alert.id)

        # 4.3 OPERATOR xem danh sách alerts
        res_op_alerts = client.get("/api/v1/alerts", headers=op_headers)
        assert res_op_alerts.status_code == 200
        assert len(res_op_alerts.json()) >= 1
        print("   [OK] Operator xem danh sách cảnh báo sự cố thành công.")

        # 4.4 OPERATOR xử lý và đóng cảnh báo (resolve)
        res_resolve_alert = client.patch(
            f"/api/v1/alerts/{test_alert.id}/resolve",
            json={"status": "RESOLVED"},
            headers=op_headers,
        )
        assert res_resolve_alert.status_code == 200
        alert_data = res_resolve_alert.json()
        assert alert_data["status"] == "RESOLVED"
        assert alert_data["resolved_by"] is not None
        assert alert_data["resolved_at"] is not None
        print(f"   [OK] Operator đóng cảnh báo #{test_alert.id} thành công (RESOLVED).")

        # ----------------------------------------------------
        # 5. KIỂM THỬ ROUTER NHẬT KÝ KIỂM TOÁN (/audit-logs)
        # ----------------------------------------------------
        print("\n[BƯỚC 5] Kiểm thử Router Nhật ký Kiểm toán (/api/v1/audit-logs)...")

        # 5.1 VIEWER & OPERATOR bị chặn 403
        res = client.get("/api/v1/audit-logs", headers=vi_headers)
        assert res.status_code == 403
        res = client.get("/api/v1/audit-logs", headers=op_headers)
        assert res.status_code == 403
        print("   [OK] Chặn thành công Viewer và Operator truy cập Audit Logs (403 Forbidden).")

        # 5.2 ADMIN xem danh sách audit logs
        res_admin_audits = client.get("/api/v1/audit-logs", headers=admin_headers)
        assert res_admin_audits.status_code == 200
        audits_list = res_admin_audits.json()
        assert len(audits_list) >= 1
        assert any(a["action"] == "RESOLVE_PLATE_MISMATCH" for a in audits_list)
        print(f"   [OK] Admin xem nhật ký kiểm toán thành công ({len(audits_list)} bản ghi, có action RESOLVE_PLATE_MISMATCH).")

        print("\n==================================================")
        print("   TẤT CẢ CÁC BƯỚC SMOKE TEST MILESTONE 3.5 ĐỀU THÀNH CÔNG RỰC RỠ!")
        print("   CORE BACKEND FASTAPI HOÀN TOÀN SẴN SÀNG CHO IOT & WEBSOCKETS!")
        print("==================================================")

    finally:
        # Dọn dẹp dữ liệu test
        cleanup_test_data(db, created_session_ids, created_alert_ids)
        db.close()


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_operational_smoke_test()
