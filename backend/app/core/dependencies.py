from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from app.models.enums import UserRole

# Chuẩn HTTP Bearer Authentication (OpenAPI scheme: bearerFormat = JWT)
# auto_error=False để chúng ta chủ động trả về 401 Unauthorized kèm header WWW-Authenticate chuẩn RFC
bearer_scheme = HTTPBearer(auto_error=False)

# Bảng phân cấp bậc vai trò: ADMIN (3) > OPERATOR (2) > VIEWER (1)
ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.VIEWER: 1,
    UserRole.OPERATOR: 2,
    UserRole.ADMIN: 3,
}


def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency trích xuất và xác thực người dùng từ Bearer JWT Token.
    Thực hiện đối soát trạng thái is_active trực tiếp từ MySQL để đảm bảo
    chặn tức thì khi tài khoản bị vô hiệu hóa (Zero-delay Revocation).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Thiếu token xác thực hoặc token không hợp lệ / đã hết hạn",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if auth is None or not auth.credentials:
        raise credentials_exception

    token = auth.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    username: str = payload.get("sub")
    if not username:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.",
        )

    return user


def require_role(min_role: UserRole):
    """
    Dependency Factory thực thi kiểm tra phân quyền RBAC theo cấp bậc tối thiểu:
    Cấp bậc cao hơn tự động kế thừa toàn bộ quyền hạn của cấp bậc thấp hơn.
    - min_role = VIEWER: Cả VIEWER, OPERATOR, ADMIN đều được phép truy cập.
    - min_role = OPERATOR: OPERATOR và ADMIN được phép truy cập.
    - min_role = ADMIN: Chỉ duy nhất ADMIN được phép truy cập.
    """
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyền hạn không đủ. Yêu cầu tối thiểu vai trò '{min_role.value}', vai trò hiện tại của bạn là '{current_user.role.value}'.",
            )
        return current_user

    return role_checker
