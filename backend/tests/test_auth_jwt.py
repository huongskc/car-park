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

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token,
)
from app.core.dependencies import get_current_user, require_role
from app.models.enums import UserRole
from app.models.user import User
from app.routers.auth import router as auth_router


def create_test_app() -> FastAPI:
    """Tạo một FastAPI test application tích hợp auth router và các test routes RBAC."""
    app = FastAPI(title="Smart Parking Test Auth App")
    app.include_router(auth_router, prefix="/api/v1")

    # Endpoint yêu cầu quyền tối thiểu: VIEWER (Mọi role hợp lệ đều vào được)
    @app.get("/api/v1/test/viewer-only")
    def test_viewer(user: User = Depends(require_role(UserRole.VIEWER))):
        return {"message": "Viewer level granted", "user": user.username, "role": user.role.value}

    # Endpoint yêu cầu quyền tối thiểu: OPERATOR (Chỉ OPERATOR và ADMIN được vào)
    @app.get("/api/v1/test/operator-only")
    def test_operator(user: User = Depends(require_role(UserRole.OPERATOR))):
        return {"message": "Operator level granted", "user": user.username, "role": user.role.value}

    # Endpoint yêu cầu quyền tối thiểu: ADMIN (Chỉ duy nhất ADMIN được vào)
    @app.get("/api/v1/test/admin-only")
    def test_admin(user: User = Depends(require_role(UserRole.ADMIN))):
        return {"message": "Admin level granted", "user": user.username, "role": user.role.value}

    return app


def run_auth_smoke_test():
    print("==================================================")
    print("   SMOKE TEST: HTTP BEARER JWT & PHÂN QUYỀN RBAC (MILESTONE 3.3)")
    print("==================================================")

    # 1. Test Bcrypt hashing
    print("[TEST 1/5] Kiểm tra hàm băm mật khẩu Bcrypt:")
    raw_pwd = "SecretPassword@2026"
    hashed = get_password_hash(raw_pwd)
    assert verify_password(raw_pwd, hashed) is True, "Bcrypt verify phải thành công với đúng mật khẩu!"
    assert verify_password("WrongPassword", hashed) is False, "Bcrypt verify phải trả về False khi sai mật khẩu!"
    print("   -> Băm và đối soát Bcrypt thành công 100%!")

    # 2. Test JWT encode/decode
    print("[TEST 2/5] Kiểm tra chu trình mã hóa & giải mã JWT:")
    dummy_payload = {"sub": "test_user", "user_id": 99, "role": "OPERATOR"}
    token = create_access_token(dummy_payload)
    decoded = decode_access_token(token)
    assert decoded is not None, "JWT decode thất bại!"
    assert decoded["sub"] == "test_user", "Payload sub không khớp!"
    assert decoded["role"] == "OPERATOR", "Payload role không khớp!"
    assert decode_access_token("fake.token.invalid") is None, "Token giả mạo phải trả về None!"
    print("   -> Mã hóa và giải mã JWT an toàn, chữ ký hợp lệ!")

    # Khởi tạo TestClient
    app = create_test_app()
    client = TestClient(app)

    # 3. Test Đăng nhập 3 tài khoản Seed Data (JSON Body chuẩn REST)
    print("[TEST 3/5] Đăng nhập 3 tài khoản seed qua API /api/v1/auth/login:")
    
    # 3.1 Admin login
    res_admin = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert res_admin.status_code == 200, f"Admin login thất bại: {res_admin.text}"
    admin_data = res_admin.json()
    admin_token = admin_data["access_token"]
    assert admin_data["role"] == "ADMIN"
    print(f"   - Đăng nhập 'admin': Thành công! Role={admin_data['role']}, UserID={admin_data['user_id']}")

    # 3.2 Operator login
    res_op = client.post("/api/v1/auth/login", json={"username": "operator", "password": "op123"})
    assert res_op.status_code == 200, f"Operator login thất bại: {res_op.text}"
    op_data = res_op.json()
    op_token = op_data["access_token"]
    assert op_data["role"] == "OPERATOR"
    print(f"   - Đăng nhập 'operator': Thành công! Role={op_data['role']}, UserID={op_data['user_id']}")

    # 3.3 Viewer login
    res_view = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "view123"})
    assert res_view.status_code == 200, f"Viewer login thất bại: {res_view.text}"
    view_data = res_view.json()
    view_token = view_data["access_token"]
    assert view_data["role"] == "VIEWER"
    print(f"   - Đăng nhập 'viewer': Thành công! Role={view_data['role']}, UserID={view_data['user_id']}")

    # 3.4 Kiểm tra endpoint GET /api/v1/auth/me với Bearer Token
    res_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_me.status_code == 200, f"GET /auth/me thất bại: {res_me.text}"
    me_data = res_me.json()
    assert me_data["username"] == "admin"
    print(f"   - GET /api/v1/auth/me trả về chính xác hồ sơ cá nhân: '{me_data['full_name']}' ({me_data['email']})")

    # 4. Test Từ chối Đăng nhập & Xác thực Bearer Token
    print("[TEST 4/5] Kiểm tra từ chối khi thông tin sai hoặc thiếu Bearer Token:")
    res_fail = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong_password_xyz"})
    assert res_fail.status_code == 401, "Phải trả về 401 Unauthorized khi sai password!"
    print(f"   -> Sai mật khẩu: Bị chặn chính xác với mã lỗi 401 ({res_fail.json()['detail']})")

    res_no_user = client.post("/api/v1/auth/login", json={"username": "ghost_user", "password": "any"})
    assert res_no_user.status_code == 401, "Phải trả về 401 khi user không tồn tại!"
    print(f"   -> User không tồn tại: Bị chặn chính xác với mã lỗi {res_no_user.status_code}")

    res_no_auth = client.get("/api/v1/auth/me")
    assert res_no_auth.status_code == 401, "Phải trả về 401 khi thiếu Header Authorization!"
    print(f"   -> Thiếu Bearer Token: Bị chặn chính xác với mã lỗi 401 ({res_no_auth.json()['detail']})")

    # 5. Test Cơ chế Phân quyền Tối thiểu (Hierarchical RBAC Guard)
    print("[TEST 5/5] Kiểm tra ma trận phân quyền tối thiểu (Hierarchy):")
    admin_hdr = {"Authorization": f"Bearer {admin_token}"}
    op_hdr = {"Authorization": f"Bearer {op_token}"}
    view_hdr = {"Authorization": f"Bearer {view_token}"}

    # 5.1 Endpoint yêu cầu VIEWER: Cả 3 role đều phải vào được
    assert client.get("/api/v1/test/viewer-only", headers=view_hdr).status_code == 200
    assert client.get("/api/v1/test/viewer-only", headers=op_hdr).status_code == 200
    assert client.get("/api/v1/test/viewer-only", headers=admin_hdr).status_code == 200
    print("   - [Quyền VIEWER]: Cả Viewer, Operator, Admin đều truy cập thành công (200 OK)!")

    # 5.2 Endpoint yêu cầu OPERATOR: Operator và Admin vào được; Viewer bị chặn 403
    assert client.get("/api/v1/test/operator-only", headers=op_hdr).status_code == 200
    assert client.get("/api/v1/test/operator-only", headers=admin_hdr).status_code == 200
    res_view_op = client.get("/api/v1/test/operator-only", headers=view_hdr)
    assert res_view_op.status_code == 403, "Viewer phải bị chặn 403 khi vào endpoint của Operator!"
    print(f"   - [Quyền OPERATOR]: Operator & Admin vào được (200 OK); Viewer bị chặn chính xác (403 Forbidden: {res_view_op.json()['detail']})!")

    # 5.3 Endpoint yêu cầu ADMIN: Chỉ Admin vào được; Operator và Viewer bị chặn 403
    assert client.get("/api/v1/test/admin-only", headers=admin_hdr).status_code == 200
    res_op_admin = client.get("/api/v1/test/admin-only", headers=op_hdr)
    assert res_op_admin.status_code == 403, "Operator phải bị chặn 403 khi vào endpoint của Admin!"
    res_view_admin = client.get("/api/v1/test/admin-only", headers=view_hdr)
    assert res_view_admin.status_code == 403, "Viewer phải bị chặn 403 khi vào endpoint của Admin!"
    print(f"   - [Quyền ADMIN]: Chỉ Admin được phép truy cập (200 OK); Operator và Viewer đều bị chặn chính xác (403 Forbidden)!")

    print("==================================================")
    print("[SUCCESS] HTTP Bearer JWT & Phân quyền RBAC hoạt động hoàn hảo!")
    print("[SUCCESS] Refactor Milestone 3.3 Verification Passed!")
    print("==================================================")


if __name__ == "__main__":
    run_auth_smoke_test()
