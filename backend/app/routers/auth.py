from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_current_user
from app.core.security import verify_password, create_access_token
from app.models.user import User
from app.models.audit import AuditLog
from app.schemas.auth_schema import LoginRequest, TokenResponse, UserOut, ProfileUpdate

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Đăng nhập hệ thống (JSON Body)",
)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Endpoint xác thực đăng nhập người dùng (Chuẩn REST JSON):
    - Tiếp nhận JSON body: `{"username": "...", "password": "..."}`.
    - Đối chiếu mật khẩu hash bcrypt trong CSDL MySQL.
    - Trả về Bearer JWT token mang thông tin định danh (sub, user_id, role).
    """
    # 1. Truy vấn người dùng từ CSDL MySQL
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không chính xác.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Kiểm tra trạng thái kích hoạt tài khoản
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản này đã bị khóa. Vui lòng liên hệ quản trị viên.",
        )

    # 3. Tạo Access Token JWT (Hybrid State-checked)
    token_payload = {
        "sub": user.username,
        "user_id": user.id,
        "role": user.role.value,
    }
    access_token = create_access_token(data=token_payload)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        role=user.role,
        username=user.username,
        full_name=user.full_name,
        user_id=user.id,
    )


@router.get(
    "/me",
    response_model=UserOut,
    summary="Lấy thông tin tài khoản đang đăng nhập",
)
def get_my_profile(
    current_user: User = Depends(get_current_user),
) -> UserOut:
    """
    Endpoint bảo vệ bằng HTTP Bearer Token:
    Trả về thông tin chi tiết của người dùng đang đăng nhập (UserOut).
    """
    return current_user


@router.patch("/me", response_model=UserOut, summary="Cập nhật hồ sơ của chính mình")
def update_my_profile(payload: ProfileUpdate,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> UserOut:
    if not payload.model_fields_set:
        raise HTTPException(400, "Cần gửi tên hoặc email cần cập nhật.")
    changes = []
    if "full_name" in payload.model_fields_set:
        name = (payload.full_name or "").strip()
        if not name:
            raise HTTPException(400, "Họ tên không được để trống.")
        if name != current_user.full_name:
            current_user.full_name = name
            changes.append("full_name")
    if "email" in payload.model_fields_set:
        email = payload.email.strip().lower() if payload.email else None
        if email and ("@" not in email or any(ch.isspace() for ch in email)):
            raise HTTPException(400, "Email không hợp lệ.")
        if email and db.query(User.id).filter(User.email == email,
                                              User.id != current_user.id).first():
            raise HTTPException(409, "Email đã được sử dụng.")
        if email != current_user.email:
            current_user.email = email
            changes.append("email")
    if changes:
        db.add(AuditLog(user_id=current_user.id, action="UPDATE_OWN_PROFILE",
                        resource_type="users", resource_id=str(current_user.id),
                        details="Đã cập nhật: " + ", ".join(changes)))
        db.commit()
        db.refresh(current_user)
    return current_user
