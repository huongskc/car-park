from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import UserRole, CommandAction, CommandStatus
from app.models.user import User
from app.models.command import Command
from app.schemas.command_schema import CommandSend, CommandOut
from app.services.command_service import command_service

router = APIRouter(
    tags=["Barrier & Device Commands"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.post(
    "/devices/{device_id}/commands",
    response_model=CommandOut,
    status_code=status.HTTP_201_CREATED,
    summary="Phát lệnh điều khiển Barrier / Thiết bị từ xa (OPERATOR & ADMIN)",
)
def send_device_command(
    device_id: str,
    payload: CommandSend,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> CommandOut:
    """
    Endpoint phát lệnh điều khiển phần cứng Barrier và bo mạch vi điều khiển ESP32:
    - `OPEN`: Mở barrier khẩn cấp (OPERATOR & ADMIN).
    - `CLOSE`: Đóng barrier cưỡng bức (OPERATOR & ADMIN).
    - `REBOOT`: Khởi động lại vi điều khiển từ xa (Chỉ ADMIN).
    - `UPDATE_CONFIG`: Đồng bộ tham số cấu hình phần cứng (Chỉ ADMIN).
    - Tự động kiểm tra an toàn thiết bị (Safety Lock): Chặn phát lệnh nếu thiết bị Offline hoặc Suspended.
    - Lưu bản ghi lệnh ở trạng thái `SENT` và bắn bản tin MQTT JSON tới ESP32.
    - Tự động ghi vết kiểm toán vào bảng `audit_logs`.
    """
    return command_service.send_command(
        device_id=device_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )


@router.get(
    "/devices/{device_id}/commands",
    response_model=list[CommandOut],
    summary="Lấy lịch sử lệnh điều khiển của thiết bị (OPERATOR & ADMIN)",
)
def list_device_commands(
    device_id: str,
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    action: Optional[CommandAction] = Query(default=None, description="Lọc theo loại hành động"),
    status_filter: Optional[CommandStatus] = Query(default=None, alias="status", description="Lọc theo trạng thái lệnh"),
    db: Session = Depends(get_db),
) -> list[CommandOut]:
    """
    Endpoint tra cứu lịch sử các lệnh điều khiển đã phát tới một thiết bị cụ thể:
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Lọc theo loại hành động (`action`) và trạng thái thực thi (`status`: SENT, ACKED, FAILED).
    - Sắp xếp giảm dần theo thời gian gửi (lệnh mới nhất lên đầu).
    """
    query = db.query(Command).filter(Command.device_id == device_id)

    if action is not None:
        query = query.filter(Command.action == action)

    if status_filter is not None:
        query = query.filter(Command.status == status_filter)

    return query.order_by(Command.sent_at.desc()).offset(skip).limit(limit).all()


@router.get(
    "/commands/{command_id}",
    response_model=CommandOut,
    summary="Tra cứu trạng thái chi tiết của một lệnh điều khiển (OPERATOR & ADMIN)",
)
def get_command_detail(
    command_id: str,
    db: Session = Depends(get_db),
) -> CommandOut:
    """
    Endpoint tra cứu trạng thái thực thi của một lệnh (kiểm tra tiến độ nhận ACK từ phần cứng).
    """
    command = db.query(Command).filter(Command.id == command_id).first()
    if not command:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy lệnh điều khiển có mã '{command_id}'.",
        )
    return command
