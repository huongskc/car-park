from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.slot import Slot
from app.models.enums import SlotStatus, UserRole
from app.models.user import User
from app.schemas.slot_schema import SlotStatusUpdate, SlotOut

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
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy ô đỗ có mã ID '{slot_id}'.",
        )

    slot.status = payload.status
    db.commit()
    db.refresh(slot)

    return slot
