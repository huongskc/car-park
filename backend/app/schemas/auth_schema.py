from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import UserRole


class LoginRequest(BaseModel):
    """Schema nhận request đăng nhập qua JSON Body."""
    username: str = Field(..., description="Tên đăng nhập hệ thống", examples=["admin"])
    password: str = Field(..., description="Mật khẩu người dùng", examples=["admin123"])


class TokenResponse(BaseModel):
    """Schema trả về access token sau khi đăng nhập thành công."""
    access_token: str = Field(..., description="JWT Bearer Token")
    token_type: str = Field(default="bearer", description="Loại token")
    role: UserRole = Field(..., description="Vai trò của người dùng")
    username: str = Field(..., description="Tên đăng nhập")
    full_name: str = Field(..., description="Họ và tên đầy đủ")
    user_id: int = Field(..., description="Mã ID người dùng")


class UserOut(BaseModel):
    """Schema trả về thông tin cá nhân của người dùng."""
    id: int
    username: str
    full_name: str
    email: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
