from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.slot import Slot
from app.models.session import ParkingSession
from app.models.telemetry import TelemetryLog
from app.models.audit import AuditLog
from app.models.enums import SlotStatus, SessionStatus, UserRole
from app.models.user import User
from app.schemas.slot_schema import SlotStatusUpdate, SlotOut
from app.schemas.telemetry_schema import TelemetryLogOut

router = APIRouter(
    prefix="/slots",
    tags=["Slots Management"],
)


@router.get(
    "",
    response_model=list[SlotOut],
    summary="Xem danh sách ô đỗ xe và trạng thái realtime (VIEWER, OPERATOR, ADMIN)",
)
def list_slots(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    site_id: Optional[str] = Query(default=None, description="Lọc theo mã khu vực bãi đỗ"),
    slot_status: Optional[SlotStatus] = Query(default=None, alias="status", description="Lọc theo trạng thái ô đỗ"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
) -> list[SlotOut]:
    """
    Endpoint tra cứu danh sách ô đỗ xe:
    - Quyền truy cập: Toàn bộ người dùng đã đăng nhập (VIEWER, OPERATOR, ADMIN).
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Hỗ trợ lọc theo `site_id` hoặc trạng thái `status` (EMPTY, OCCUPIED, RESERVED, MAINTENANCE).
    - Trả về thông tin trạng thái và khoảng cách đo được gần nhất từ cảm biến siêu âm.
    """
    query = db.query(Slot)

    if site_id:
        query = query.filter(Slot.site_id == site_id)

    if slot_status is not None:
        query = query.filter(Slot.status == slot_status)

    return query.order_by(Slot.id.asc()).offset(skip).limit(limit).all()


@router.patch(
    "/{slot_id}/status",
    response_model=SlotOut,
    summary="Cập nhật trạng thái ô đỗ (OPERATOR & ADMIN)",
)
def update_slot_status(
    slot_id: str,
    payload: SlotStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> SlotOut:
    """
    Endpoint cập nhật thủ công trạng thái ô đỗ:
    - Quyền truy cập: Chỉ OPERATOR và ADMIN.
    - Cho phép chuyển đổi trạng thái (ví dụ: chuyển sang MAINTENANCE khi cảm biến hỏng hoặc RESERVED khi bảo lưu).
    """
    slot = db.query(Slot).filter(Slot.id == slot_id).with_for_update().first()
    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy ô đỗ có mã ID '{slot_id}'.",
        )

    active_session = (
        db.query(ParkingSession)
        .filter(
            ParkingSession.slot_id == slot_id,
            ParkingSession.status.in_([SessionStatus.ACTIVE, SessionStatus.PENDING_REVIEW]),
        )
        .with_for_update()
        .first()
    )
    if active_session and payload.status != SlotStatus.OCCUPIED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Không thể cập nhật ô đỗ '{slot_id}' sang {payload.status.value} vì đang có phiên gửi xe chưa kết thúc "
                f"(Phiên #{active_session.session_code}, trạng thái: {active_session.status.value}). "
                "Vui lòng hoàn tất thanh toán (checkout) hoặc hủy phiên trước khi đổi trạng thái ô."
            ),
        )

    old_status = slot.status
    slot.status = payload.status

    # Ghi nhận vết kiểm toán
    audit = AuditLog(
        user_id=current_user.id,
        action="UPDATE_SLOT_STATUS",
        resource_type="slots",
        resource_id=slot.id,
        details=f"Cập nhật trạng thái ô đỗ '{slot.id}' từ {old_status.value} sang {payload.status.value}",
    )
    db.add(audit)

    db.commit()
    db.refresh(slot)

    return slot


@router.get(
    "/{slot_id}/telemetry",
    response_model=list[TelemetryLogOut],
    summary="Xem lịch sử đo lường cảm biến siêu âm theo ô đỗ (VIEWER, OPERATOR, ADMIN)",
)
def get_slot_telemetry(
    slot_id: str,
    limit: int = Query(default=40, ge=1, le=500, description="Số lượng bản ghi tối đa"),
    minutes: Optional[int] = Query(default=None, ge=1, le=1440, description="Lọc dữ liệu trong N phút gần nhất"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
) -> list[TelemetryLogOut]:
    """
    Endpoint tra cứu lịch sử đo khoảng cách cảm biến siêu âm của một ô đỗ:
    - Quyền truy cập: VIEWER, OPERATOR, ADMIN.
    - Kiểm tra ô đỗ tồn tại trong hệ thống (trả về 404 nếu không tìm thấy).
    - Lấy N bản ghi mới nhất và trả về theo thứ tự thời gian tăng dần (sampled_at.asc())
      giúp Frontend dễ dàng vẽ đồ thị Sparkline trực tiếp.
    """
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy ô đỗ có mã ID '{slot_id}'.",
        )

    query = db.query(TelemetryLog).filter(TelemetryLog.slot_id == slot_id)
    if minutes is not None:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        query = query.filter(TelemetryLog.sampled_at >= cutoff)

    records = (
        query.order_by(TelemetryLog.sampled_at.desc(), TelemetryLog.id.desc())
        .limit(limit)
        .all()
    )
    records.reverse()
    return records
