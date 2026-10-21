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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.user import User
from app.models.card import Card
from app.models.slot import Slot
from app.models.enums import UserRole, CardType, SlotStatus
from app.routers import (
    auth_router,
    users_router,
    cards_router,
    slots_router,
)


def create_test_app() -> FastAPI:
    """Tạo FastAPI application tích hợp đầy đủ 4 routers quản trị dữ liệu nguồn."""
    app = FastAPI(title="Smart Parking Test Master Data App")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(cards_router, prefix="/api/v1")
    app.include_router(slots_router, prefix="/api/v1")
    return app


def cleanup_test_records(db, test_username: str, test_card_uid: str):
    """Dọn dẹp bản ghi test tạm để giữ CSDL sạch cho các lần chạy sau."""
    try:
        user = db.query(User).filter(User.username == test_username).first()
        if user:
            db.delete(user)
        card = db.query(Card).filter(Card.uid == test_card_uid).first()
        if card:
            db.delete(card)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"   [CẢNH BÁO] Không thể dọn dẹp dữ liệu test: {e}")


def run_master_data_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: ROUTERS DỮ LIỆU NGUỒN (MILESTONE 3.4)")
    print("   USERS, CARDS, SLOTS & HIERARCHICAL RBAC")
    print("==================================================")

    app = create_test_app()
    client = TestClient(app)
    db = SessionLocal()

    test_username = "test_op_temp_99"
    test_card_uid = "TEST_RFID_9999"

    # Dọn dẹp trước nếu còn sót từ lần chạy trước
    cleanup_test_records(db, test_username, test_card_uid)

    try:
        # ----------------------------------------------------
        # 1. Đăng nhập lấy Bearer Token của cả 3 tài khoản mẫu
        # ----------------------------------------------------
        print("\n[BƯỚC 1] Đăng nhập xác thực 3 vai trò mẫu...")
        
        login_res_admin = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login_res_admin.status_code == 200, f"Lỗi login admin: {login_res_admin.text}"
        admin_token = login_res_admin.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        print("   [OK] Admin đăng nhập thành công.")

        login_res_op = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
        assert login_res_op.status_code == 200, f"Lỗi login operator: {login_res_op.text}"
        op_token = login_res_op.json()["access_token"]
        op_headers = {"Authorization": f"Bearer {op_token}"}
        print("   [OK] Operator đăng nhập thành công.")

        login_res_vi = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
        assert login_res_vi.status_code == 200, f"Lỗi login viewer: {login_res_vi.text}"
        vi_token = login_res_vi.json()["access_token"]
        vi_headers = {"Authorization": f"Bearer {vi_token}"}
        print("   [OK] Viewer đăng nhập thành công.")

        # ----------------------------------------------------
        # 2. KIỂM THỬ ROUTER QUẢN TRỊ NGƯỜI DÙNG (/users)
        # ----------------------------------------------------
        print("\n[BƯỚC 2] Kiểm thử Router Quản trị Người dùng (/api/v1/users)...")

        # 2.1 Kiểm tra RBAC: VIEWER & OPERATOR bị chặn 403
        res = client.get("/api/v1/users", headers=vi_headers)
        assert res.status_code == 403, f"Viewer không được phép truy cập users, code nhận: {res.status_code}"
        res = client.get("/api/v1/users", headers=op_headers)
        assert res.status_code == 403, f"Operator không được phép truy cập users, code nhận: {res.status_code}"
        print("   [OK] Chặn thành công 403 Forbidden đối với Viewer và Operator.")

        # 2.2 ADMIN xem danh sách người dùng
        res = client.get("/api/v1/users", headers=admin_headers)
        assert res.status_code == 200, f"Admin phải xem được users: {res.text}"
        users_list = res.json()
        assert len(users_list) >= 3, "Danh sách người dùng phải chứa tối thiểu 3 seed users."
        print(f"   [OK] Admin lấy danh sách thành công ({len(users_list)} người dùng).")

        # 2.3 Tìm kiếm & lọc users
        res = client.get("/api/v1/users?search=admin", headers=admin_headers)
        assert res.status_code == 200 and len(res.json()) >= 1
        res = client.get("/api/v1/users?role=OPERATOR", headers=admin_headers)
        assert res.status_code == 200 and any(u["role"] == "OPERATOR" for u in res.json())
        print("   [OK] Tìm kiếm (search=admin) và lọc (role=OPERATOR) hoạt động chính xác.")

        # 2.4 ADMIN tạo mới người dùng
        new_user_payload = {
            "username": test_username,
            "password": "tempPassword99",
            "full_name": "Nhân viên Vận hành Test",
            "email": "test_op99@ptit.edu.vn",
            "role": "OPERATOR"
        }
        res = client.post("/api/v1/users", json=new_user_payload, headers=admin_headers)
        assert res.status_code == 201, f"Tạo user thất bại: {res.text}"
        created_user = res.json()
        test_user_id = created_user["id"]
        assert created_user["username"] == test_username
        assert created_user["is_active"] is True
        print(f"   [OK] Tạo mới người dùng '{test_username}' thành công (ID: {test_user_id}).")

        # 2.5 Kiểm tra tính duy nhất (Trùng lặp username)
        res_dup = client.post("/api/v1/users", json=new_user_payload, headers=admin_headers)
        assert res_dup.status_code == 400, "Phải báo 400 Bad Request khi trùng lặp username."
        print("   [OK] Chặn thành công 400 Bad Request khi trùng lặp username.")

        # 2.6 Khóa mềm tài khoản (is_active = False)
        res_lock = client.patch(f"/api/v1/users/{test_user_id}/status", json={"is_active": False}, headers=admin_headers)
        assert res_lock.status_code == 200 and res_lock.json()["is_active"] is False
        print(f"   [OK] Khóa mềm tài khoản ID {test_user_id} thành công (is_active=False).")

        # Kiểm chứng Zero-delay Revocation: User bị khóa không thể đăng nhập
        res_locked_login = client.post("/api/v1/auth/login", json={"username": test_username, "password": "tempPassword99"})
        assert res_locked_login.status_code == 403, "Tài khoản bị khóa phải bị từ chối đăng nhập (403 Forbidden)."
        print("   [OK] Zero-delay Revocation: Tài khoản bị khóa bị từ chối đăng nhập 403 Forbidden.")

        # 2.7 Mở khóa tài khoản (is_active = True)
        res_unlock = client.patch(f"/api/v1/users/{test_user_id}/status", json={"is_active": True}, headers=admin_headers)
        assert res_unlock.status_code == 200 and res_unlock.json()["is_active"] is True
        print(f"   [OK] Mở khóa tài khoản ID {test_user_id} thành công.")

        # ----------------------------------------------------
        # 3. KIỂM THỬ ROUTER QUẢN TRỊ THẺ RFID (/cards)
        # ----------------------------------------------------
        print("\n[BƯỚC 3] Kiểm thử Router Quản trị Thẻ RFID (/api/v1/cards)...")

        # 3.1 VIEWER bị chặn 403
        res = client.get("/api/v1/cards", headers=vi_headers)
        assert res.status_code == 403, "Viewer không được phép quản lý thẻ (403 Forbidden)."
        print("   [OK] Chặn thành công Viewer truy cập API thẻ RFID (403 Forbidden).")

        # 3.2 OPERATOR và ADMIN được phép xem
        res_op_cards = client.get("/api/v1/cards", headers=op_headers)
        assert res_op_cards.status_code == 200
        res_admin_cards = client.get("/api/v1/cards", headers=admin_headers)
        assert res_admin_cards.status_code == 200
        print("   [OK] Operator và Admin (thừa hưởng cấp bậc) lấy danh sách thẻ thành công.")

        # 3.3 OPERATOR đăng ký thẻ RFID mới
        new_card_payload = {
            "uid": test_card_uid,
            "card_type": "MONTHLY",
            "owner_name": "Chủ Xe Thử Nghiệm",
            "registered_plate": "30F-999.88"
        }
        res_card_create = client.post("/api/v1/cards", json=new_card_payload, headers=op_headers)
        assert res_card_create.status_code == 201, f"Đăng ký thẻ thất bại: {res_card_create.text}"
        assert res_card_create.json()["uid"] == test_card_uid
        print(f"   [OK] Operator tạo mới thẻ RFID '{test_card_uid}' thành công.")

        # 3.4 Chặn trùng lặp UID thẻ
        res_card_dup = client.post("/api/v1/cards", json=new_card_payload, headers=op_headers)
        assert res_card_dup.status_code == 400
        print("   [OK] Chặn thành công 400 Bad Request khi trùng lặp UID thẻ.")

        # 3.5 Tìm kiếm thẻ RFID đa năng
        res_search_uid = client.get(f"/api/v1/cards?search={test_card_uid}", headers=op_headers)
        assert len(res_search_uid.json()) >= 1
        res_search_plate = client.get("/api/v1/cards?search=30F-999.88", headers=op_headers)
        assert len(res_search_plate.json()) >= 1
        print("   [OK] Tìm kiếm linh hoạt thẻ theo UID và Biển số xe thành công.")

        # 3.6 Khóa thẻ RFID (báo mất thẻ)
        res_card_lock = client.patch(f"/api/v1/cards/{test_card_uid}/status", json={"is_active": False}, headers=op_headers)
        assert res_card_lock.status_code == 200 and res_card_lock.json()["is_active"] is False
        print(f"   [OK] Khóa thẻ RFID '{test_card_uid}' (báo mất) thành công (is_active=False).")

        # ----------------------------------------------------
        # 4. KIỂM THỬ ROUTER QUẢN LÝ Ô ĐỖ (/slots)
        # ----------------------------------------------------
        print("\n[BƯỚC 4] Kiểm thử Router Quản lý Ô đỗ (/api/v1/slots)...")

        # 4.1 Chưa đăng nhập (Không có token) bị 401
        res_unauth = client.get("/api/v1/slots")
        assert res_unauth.status_code == 401, "Truy cập không token phải nhận 401 Unauthorized."
        print("   [OK] Chưa đăng nhập bị từ chối 401 Unauthorized.")

        # 4.2 Cả VIEWER, OPERATOR, ADMIN đều xem được danh sách ô đỗ
        res_vi_slots = client.get("/api/v1/slots", headers=vi_headers)
        assert res_vi_slots.status_code == 200
        slots_data = res_vi_slots.json()
        assert len(slots_data) >= 1, "Hệ thống phải có ít nhất 1 ô đỗ từ seed data."
        target_slot_id = slots_data[0]["id"]
        original_status = slots_data[0]["status"]
        print(f"   [OK] Viewer xem được danh sách ({len(slots_data)} ô đỗ, kiểm thử trên ô '{target_slot_id}').")

        # 4.3 Phân quyền sửa trạng thái ô đỗ: VIEWER bị chặn 403
        res_vi_patch = client.patch(
            f"/api/v1/slots/{target_slot_id}/status",
            json={"status": "MAINTENANCE"},
            headers=vi_headers,
        )
        assert res_vi_patch.status_code == 403, "Viewer không được phép sửa trạng thái ô đỗ (403 Forbidden)."
        print("   [OK] Chặn thành công Viewer sửa trạng thái ô đỗ (403 Forbidden).")

        # 4.4 OPERATOR chuyển trạng thái sang MAINTENANCE (Bảo trì)
        res_op_patch = client.patch(
            f"/api/v1/slots/{target_slot_id}/status",
            json={"status": "MAINTENANCE"},
            headers=op_headers,
        )
        assert res_op_patch.status_code == 200 and res_op_patch.json()["status"] == "MAINTENANCE"
        print(f"   [OK] Operator đổi trạng thái ô '{target_slot_id}' sang MAINTENANCE thành công.")

        # 4.5 ADMIN khôi phục trạng thái ban đầu của ô đỗ
        res_admin_restore = client.patch(
            f"/api/v1/slots/{target_slot_id}/status",
            json={"status": original_status},
            headers=admin_headers,
        )
        assert res_admin_restore.status_code == 200 and res_admin_restore.json()["status"] == original_status
        print(f"   [OK] Admin (thừa hưởng cấp bậc) khôi phục trạng thái ban đầu của ô '{target_slot_id}'.")

        print("\n==================================================")
        print("   TẤT CẢ CÁC BƯỚC SMOKE TEST MILESTONE 3.4 ĐỀU THÀNH CÔNG RỰC RỠ!")
        print("==================================================")

    finally:
        # Dọn dẹp dữ liệu test tạm
        cleanup_test_records(db, test_username, test_card_uid)
        db.close()


if __name__ == "__main__":
    from smoke_guard import require_test_database
    require_test_database()
    run_master_data_smoke_test()
