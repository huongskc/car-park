from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.core.security import get_password_hash
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.user_schema import UserCreate, UserStatusUpdate, UserOut

router = APIRouter(
    prefix="/users",
    tags=["Users Management"],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


@router.get(
    "",
    response_model=list[UserOut],
    summary="Lấy danh sách người dùng trong hệ thống (ADMIN)",
)
def list_users(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    role: Optional[UserRole] = Query(default=None, description="Lọc theo vai trò người dùng"),
    is_active: Optional[bool] = Query(default=None, description="Lọc theo trạng thái kích hoạt"),
    search: Optional[str] = Query(default=None, description="Tìm kiếm theo username, họ tên hoặc email"),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    """
    Endpoint quản trị danh sách người dùng (Chỉ ADMIN):
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Lọc chính xác theo `role` và `is_active`.
    - Tìm kiếm gần đúng (LIKE) trên `username`, `full_name` và `email`.
    """
    query = db.query(User)

    if role is not None:
        query = query.filter(User.role == role)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                User.username.ilike(search_pattern),
                User.full_name.ilike(search_pattern),
                User.email.ilike(search_pattern),
            )
        )

    return query.order_by(User.id.asc()).offset(skip).limit(limit).all()


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo mới tài khoản người dùng (ADMIN)",
)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
) -> UserOut:
    """
    Endpoint tạo mới tài khoản người dùng:
    - Kiểm tra tính duy nhất của `username` và `email`.
    - Băm mật khẩu một chiều an toàn bằng bcrypt.
    - Mặc định tài khoản được kích hoạt (`is_active = True`).
    """
    # 1. Kiểm tra username trùng lặp
    existing_username = db.query(User).filter(User.username == payload.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tên đăng nhập '{payload.username}' đã tồn tại trong hệ thống.",
        )

    # 2. Kiểm tra email trùng lặp (nếu được cung cấp)
    if payload.email:
        existing_email = db.query(User).filter(User.email == payload.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Địa chỉ email '{payload.email}' đã được sử dụng bởi người dùng khác.",
            )

    # 3. Băm mật khẩu an toàn
    hashed_pwd = get_password_hash(payload.password)

    # 4. Lưu bản ghi người dùng mới
    new_user = User(
        username=payload.username,
        password_hash=hashed_pwd,
        full_name=payload.full_name,
        email=payload.email,
        role=payload.role,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@router.patch(
    "/{user_id}/status",
    response_model=UserOut,
    summary="Cập nhật trạng thái kích hoạt (Khóa mềm / Mở khóa) người dùng (ADMIN)",
)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
) -> UserOut:
    """
    Endpoint khóa mềm hoặc mở khóa tài khoản người dùng:
    - Chuyển `is_active = False` để khóa (Zero-delay Revocation).
    - Chuyển `is_active = True` để kích hoạt lại.
    - Không xóa cứng khỏi cơ sở dữ liệu để bảo tồn lịch sử Audit Logs và Phiên gửi xe.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy người dùng có ID {user_id}.",
        )

    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)

    return user
