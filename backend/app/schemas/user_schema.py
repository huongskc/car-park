from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import UserRole
from app.schemas.auth_schema import UserOut


class UserCreate(BaseModel):
    """Schema tạo mới người dùng trong hệ thống (dành cho ADMIN)."""
    username: str = Field(..., min_length=3, max_length=50, description="Tên đăng nhập hệ thống", examples=["operator_02"])
    password: str = Field(..., min_length=6, max_length=100, description="Mật khẩu khởi tạo", examples=["secret123"])
    full_name: str = Field(..., min_length=1, max_length=100, description="Họ và tên đầy đủ", examples=["Trần Thị Lan"])
    email: Optional[str] = Field(None, max_length=100, description="Địa chỉ email liên hệ", examples=["lan.tran@example.com"])
    role: UserRole = Field(default=UserRole.VIEWER, description="Vai trò hệ thống (ADMIN, OPERATOR, VIEWER)")


class UserStatusUpdate(BaseModel):
    """Schema cập nhật trạng thái hoạt động (khóa mềm / mở khóa) của người dùng."""
    is_active: bool = Field(..., description="Trạng thái kích hoạt (True: hoạt động, False: bị khóa)")


__all__ = [
    "UserCreate",
    "UserStatusUpdate",
    "UserOut",
]
