import secrets
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import UserRole, DeviceStatus
from app.models.user import User
from app.models.device import Device, DeviceConfig
from app.models.audit import AuditLog
from app.schemas.device_schema import (
    DeviceRegister,
    DeviceConfigUpdate,
    DeviceStatusChange,
    DeviceOut,
)

router = APIRouter(
    prefix="/devices",
    tags=["Devices Management"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.get(
    "",
    response_model=list[DeviceOut],
    summary="Lấy danh sách thiết bị IoT (OPERATOR & ADMIN)",
)
def list_devices(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    status_filter: Optional[DeviceStatus] = Query(default=None, alias="status", description="Lọc theo trạng thái thiết bị"),
    search: Optional[str] = Query(default=None, description="Tìm kiếm theo mã thiết bị, tên, phân vùng hoặc IP"),
    db: Session = Depends(get_db),
) -> list[DeviceOut]:
    """
    Endpoint tra cứu danh sách thiết bị phần cứng IoT:
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Lọc theo trạng thái vòng đời (`status`).
    - Tìm kiếm gần đúng (LIKE) trên `id`, `name`, `site_id`, và `ip_address`.
    - Trả về danh sách thiết bị kèm theo thông số cấu hình (`config`).
    """
    query = db.query(Device)

    if status_filter is not None:
        query = query.filter(Device.status == status_filter)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Device.id.ilike(pattern),
                Device.name.ilike(pattern),
                Device.site_id.ilike(pattern),
                Device.ip_address.ilike(pattern),
            )
        )

    return query.order_by(Device.created_at.desc()).offset(skip).limit(limit).all()


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký Onboarding thiết bị mới và cấp Token (ADMIN)",
)
def register_device(
    payload: DeviceRegister,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> DeviceOut:
    """
    Endpoint onboarding thiết bị IoT vào hệ thống (Bước 1, 2, 3 Vòng đời):
    - Kiểm tra tính duy nhất của mã định danh thiết bị (`id`).
    - Sinh mã xác thực an toàn `auth_token` (`dtk_<random>`) để nạp vào firmware ESP32.
    - Khởi tạo thiết bị ở trạng thái `REGISTERED`.
    - Tự động tạo bản ghi tham số cấu hình mặc định trong bảng `device_configs`.
    - Ghi nhận lịch sử kiểm toán vào bảng `audit_logs`.
    """
    existing_device = db.query(Device).filter(Device.id == payload.id).first()
    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Thiết bị với mã ID '{payload.id}' đã tồn tại trong hệ thống.",
        )

    # Sinh token định danh thiết bị (KISS Ponytail)
    generated_token = f"dtk_{secrets.token_urlsafe(24)}"

    new_device = Device(
        id=payload.id,
        name=payload.name,
        site_id=payload.site_id,
        auth_token=generated_token,
        status=DeviceStatus.REGISTERED,
        is_online=False,
        ip_address=payload.ip_address,
        hw_version=payload.hw_version,
        fw_version=payload.fw_version,
    )
    db.add(new_device)

    # Tạo bản ghi cấu hình mặc định ban đầu
    default_config = DeviceConfig(
        device_id=payload.id,
        distance_threshold_cm=15.00,
        servo_open_time_sec=10,
        heartbeat_interval_sec=10,
        buzzer_enabled=True,
        version=1,
    )
    db.add(default_config)

    # Ghi nhận Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="REGISTER_DEVICE",
        resource_type="devices",
        resource_id=new_device.id,
        details=f"Đăng ký onboarding thiết bị {new_device.id} ({new_device.name}) tại site {new_device.site_id}",
    )
    db.add(audit)

    db.commit()
    db.refresh(new_device)

    return new_device


@router.get(
    "/{device_id}",
    response_model=DeviceOut,
    summary="Lấy thông tin chi tiết thiết bị IoT (OPERATOR & ADMIN)",
)
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
) -> DeviceOut:
    """
    Endpoint tra cứu thông tin chi tiết một thiết bị kèm tham số cấu hình:
    - Trả về mã thiết bị, token, trạng thái online/offline, phiên bản phần mềm/phần cứng.
    - Kèm theo thông số cấu hình ngưỡng khoảng cách, thời gian mở barrier, chu kỳ heartbeat.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy thiết bị có ID '{device_id}'.",
        )
    return device


@router.patch(
    "/{device_id}/status",
    response_model=DeviceOut,
    summary="Chuyển đổi trạng thái vòng đời thiết bị (ADMIN)",
)
def change_device_status(
    device_id: str,
    payload: DeviceStatusChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> DeviceOut:
    """
    Endpoint chuyển đổi trạng thái vòng đời thiết bị (Bước 4, 8, 9, 10 Vòng đời):
    - `ACTIVATED`: Đưa thiết bị vào vận hành chính thức.
    - `MAINTENANCE`: Đưa vào bảo trì kỹ thuật, kiểm tra cảm biến/barrier.
    - `SUSPENDED`: Tạm dừng hoạt động khẩn cấp do sự cố.
    - `DECOMMISSIONED`: Khai tử / Ngừng sử dụng vĩnh viễn.
    - QUY TẮC BẤT BIẾN: Thiết bị đã ở trạng thái DECOMMISSIONED bị khóa một chiều, không thể chuyển đổi trạng thái khác.
    - Ghi nhận Audit Log mọi lần thay đổi trạng thái.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy thiết bị có ID '{device_id}'.",
        )

    # Kiểm tra quy tắc bất biến: DECOMMISSIONED là trạng thái kết thúc (Terminal State)
    if device.status == DeviceStatus.DECOMMISSIONED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Thiết bị '{device_id}' đã bị ngừng hoạt động vĩnh viễn (DECOMMISSIONED) và không thể chuyển sang trạng thái khác.",
        )

    old_status = device.status
    device.status = payload.status

    # Nếu đưa vào bảo trì, tạm dừng hoặc khai tử, cập nhật is_online = False
    if payload.status in [DeviceStatus.MAINTENANCE, DeviceStatus.SUSPENDED, DeviceStatus.DECOMMISSIONED]:
        device.is_online = False

    # Ghi nhận Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="CHANGE_DEVICE_STATUS",
        resource_type="devices",
        resource_id=device.id,
        details=f"Chuyển trạng thái từ {old_status.value} sang {payload.status.value}. Lý do: {payload.reason or 'Không có'}",
    )
    db.add(audit)

    db.commit()
    db.refresh(device)

    return device


@router.patch(
    "/{device_id}/config",
    response_model=DeviceOut,
    summary="Cập nhật tham số cấu hình từ xa cho thiết bị (ADMIN)",
)
def update_device_config(
    device_id: str,
    payload: DeviceConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> DeviceOut:
    """
    Endpoint cập nhật tham số cấu hình từ xa (Bước 5 Vòng đời thiết bị):
    - Điều chỉnh ngưỡng khoảng cách cảm biến siêu âm (`distance_threshold_cm`).
    - Điều chỉnh thời gian mở barrier servo (`servo_open_time_sec`).
    - Điều chỉnh chu kỳ gửi nhịp tim (`heartbeat_interval_sec`).
    - Bật/tắt còi buzzer cảnh báo (`buzzer_enabled`).
    - Tự động tăng phiên bản cấu hình (`version = version + 1`) để hỗ trợ đồng bộ MQTT Config.
    - Ghi nhận Audit Log.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy thiết bị có ID '{device_id}'.",
        )

    config = db.query(DeviceConfig).filter(DeviceConfig.device_id == device_id).first()
    if not config:
        config = DeviceConfig(device_id=device_id, version=1)
        db.add(config)

    if payload.distance_threshold_cm is not None:
        config.distance_threshold_cm = payload.distance_threshold_cm
    if payload.servo_open_time_sec is not None:
        config.servo_open_time_sec = payload.servo_open_time_sec
    if payload.heartbeat_interval_sec is not None:
        config.heartbeat_interval_sec = payload.heartbeat_interval_sec
    if payload.buzzer_enabled is not None:
        config.buzzer_enabled = payload.buzzer_enabled

    config.version += 1

    # Ghi nhận Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="UPDATE_DEVICE_CONFIG",
        resource_type="device_configs",
        resource_id=str(config.id if config.id else device_id),
        details=f"Cập nhật cấu hình thiết bị {device_id} lên version {config.version}",
    )
    db.add(audit)

    db.commit()
    db.refresh(device)

    return device
