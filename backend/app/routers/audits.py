from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import UserRole
from app.models.audit import AuditLog
from app.schemas.audit_schema import AuditLogOut

router = APIRouter(
    prefix="/audit-logs",
    tags=["Audit Logs"],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)


@router.get(
    "",
    response_model=list[AuditLogOut],
    summary="Xem nhật ký kiểm toán hệ thống (ADMIN)",
)
def list_audit_logs(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    user_id: Optional[int] = Query(default=None, description="Lọc theo mã ID người thực hiện"),
    action: Optional[str] = Query(default=None, description="Lọc theo hành vi tác động (ví dụ: RESOLVE_PLATE_MISMATCH)"),
    resource_type: Optional[str] = Query(default=None, description="Lọc theo loại tài nguyên tác động"),
    db: Session = Depends(get_db),
) -> list[AuditLogOut]:
    """
    Endpoint tra cứu nhật ký kiểm toán hệ thống (Chỉ dành cho ADMIN):
    - Ghi lại các thao tác nhạy cảm (duyệt ngoại lệ, cấu hình, xử lý sự cố).
    - Hỗ trợ lọc theo `user_id`, `action`, `resource_type`.
    - Sắp xếp giảm dần theo thời gian (mới nhất lên đầu).
    """
    query = db.query(AuditLog)

    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)

    if action:
        query = query.filter(AuditLog.action == action.strip())

    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type.strip())

    return query.order_by(AuditLog.id.desc()).offset(skip).limit(limit).all()
