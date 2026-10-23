from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.telemetry import TelemetryLog
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.telemetry_schema import TelemetryLogOut

router = APIRouter(
    prefix="/telemetry",
    tags=["Telemetry"],
)


@router.get(
    "/history",
    response_model=list[TelemetryLogOut],
    summary="Xem lịch sử đo lường cảm biến siêu âm tổng quát (VIEWER, OPERATOR, ADMIN)",
)
def get_telemetry_history(
    slot_id: Optional[str] = Query(default=None, description="Lọc theo mã ô đỗ"),
    device_id: Optional[str] = Query(default=None, description="Lọc theo mã thiết bị ESP32"),
    limit: int = Query(default=40, ge=1, le=500, description="Số lượng bản ghi tối đa"),
    minutes: Optional[int] = Query(default=None, ge=1, le=1440, description="Lọc dữ liệu trong N phút gần nhất"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.VIEWER)),
) -> list[TelemetryLogOut]:
    """
    Endpoint tra cứu lịch sử đo khoảng cách cảm biến siêu âm tổng quát:
    - Quyền truy cập: VIEWER, OPERATOR, ADMIN.
    - Hỗ trợ lọc theo `slot_id`, `device_id`, và khoảng thời gian `minutes` gần nhất.
    - Lấy N bản ghi mới nhất và trả về theo thứ tự thời gian tăng dần (sampled_at.asc())
      phục vụ trực quan hóa dữ liệu và giám sát hệ thống.
    """
    query = db.query(TelemetryLog)

    if slot_id:
        query = query.filter(TelemetryLog.slot_id == slot_id)

    if device_id:
        query = query.filter(TelemetryLog.device_id == device_id)

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
