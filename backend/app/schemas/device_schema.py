from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import DeviceStatus


class DeviceRegister(BaseModel):
    """Schema đăng ký thiết bị mới (Bước 1 & Bước 2 Vòng đời thiết bị)."""
    id: str = Field(..., min_length=3, max_length=50, description="Mã định danh phần cứng thiết bị", examples=["ESP32_GATE_01"])
    name: str = Field(..., min_length=1, max_length=100, description="Tên gợi nhớ của thiết bị", examples=["Bộ điều khiển Cổng vào & Cảm biến Siêu âm"])
    site_id: str = Field(default="PTIT_ZONE_1", max_length=50, description="Mã phân vùng bãi đỗ (Bước 3 Vòng đời)")
    ip_address: Optional[str] = Field(None, max_length=45, description="Địa chỉ IP tĩnh hoặc DHCP", examples=["192.168.1.101"])
    hw_version: str = Field(default="ESP32-WROOM-32", max_length=50, description="Phiên bản phần cứng / bo mạch vi điều khiển")
    fw_version: str = Field(default="1.0.0", max_length=20, description="Phiên bản firmware nạp ban đầu")


class DeviceConfigUpdate(BaseModel):
    """Schema cập nhật tham số cấu hình từ xa (Bước 5 Vòng đời thiết bị)."""
    distance_threshold_cm: Optional[float] = Field(None, ge=1.0, le=500.0, description="Ngưỡng khoảng cách nhận diện xe (cm)")
    servo_open_time_sec: Optional[int] = Field(None, ge=1, le=120, description="Thời gian mở barrier trước khi tự động đóng (giây)")
    heartbeat_interval_sec: Optional[int] = Field(None, ge=5, le=300, description="Chu kỳ gửi bản tin Heartbeat (giây)")
    buzzer_enabled: Optional[bool] = Field(None, description="Bật/tắt còi buzzer cảnh báo trên bo mạch")


class DeviceStatusChange(BaseModel):
    """Schema chuyển đổi trạng thái vòng đời thiết bị (Bước 4, 8, 9, 10)."""
    status: DeviceStatus = Field(..., description="Trạng thái mục tiêu (ACTIVATED, MAINTENANCE, SUSPENDED, DECOMMISSIONED)")
    reason: Optional[str] = Field(None, max_length=255, description="Lý do chuyển trạng thái (ví dụ: bảo trì định kỳ, cảm biến hỏng)")


class DeviceConfigOut(BaseModel):
    """Schema trả về thông tin cấu hình tham số thiết bị."""
    id: int
    device_id: str
    distance_threshold_cm: float
    servo_open_time_sec: int
    heartbeat_interval_sec: int
    buzzer_enabled: bool
    version: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeviceOut(BaseModel):
    """Schema trả về thông tin chi tiết thiết bị IoT cho danh sách và chi tiết thông thường (không lộ auth_token)."""
    id: str
    name: str
    site_id: str
    status: DeviceStatus
    is_online: bool
    ip_address: Optional[str] = None
    fw_version: Optional[str] = None
    hw_version: Optional[str] = None
    last_heartbeat_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    config: Optional[DeviceConfigOut] = None

    model_config = ConfigDict(from_attributes=True)


class DeviceRegisterOut(DeviceOut):
    """Schema trả về khi đăng ký thiết bị mới (chỉ cấp auth_token duy nhất một lần khi onboarding)."""
    auth_token: str


__all__ = [
    "DeviceRegister",
    "DeviceRegisterOut",
    "DeviceConfigUpdate",
    "DeviceStatusChange",
    "DeviceConfigOut",
    "DeviceOut",
]
