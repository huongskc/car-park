from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import UserRole, AlertType, AlertSeverity, AlertStatus
from app.models.user import User
from app.models.alert import Alert
from app.schemas.alert_schema import AlertResolve, AlertOut

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts Management"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.get(
    "",
    response_model=list[AlertOut],
    summary="Lấy danh sách cảnh báo sự cố (OPERATOR & ADMIN)",
)
def list_alerts(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    status_filter: Optional[AlertStatus] = Query(default=None, alias="status", description="Lọc theo trạng thái cảnh báo"),
    severity_filter: Optional[AlertSeverity] = Query(default=None, alias="severity", description="Lọc theo mức độ nghiêm trọng"),
    alert_type_filter: Optional[AlertType] = Query(default=None, alias="alert_type", description="Lọc theo loại cảnh báo"),
    db: Session = Depends(get_db),
) -> list[AlertOut]:
    """
    Endpoint tra cứu danh sách cảnh báo sự cố trong hệ thống:
    - Lọc theo trạng thái (NEW, ACKNOWLEDGED, RESOLVED, DISMISSED).
    - Lọc theo mức độ (INFO, WARNING, CRITICAL) và loại sự cố (PLATE_MISMATCH, UNAUTHORIZED_CARD, v.v.).
    - Sắp xếp giảm dần theo thời gian phát sinh sự cố (mới nhất lên đầu).
    """
    query = db.query(Alert)

    if status_filter is not None:
        query = query.filter(Alert.status == status_filter)

    if severity_filter is not None:
        query = query.filter(Alert.severity == severity_filter)

    if alert_type_filter is not None:
        query = query.filter(Alert.alert_type == alert_type_filter)

    return query.order_by(Alert.created_at.desc()).offset(skip).limit(limit).all()


@router.patch(
    "/{alert_id}/resolve",
    response_model=AlertOut,
    summary="Xử lý và đóng cảnh báo sự cố (OPERATOR & ADMIN)",
)
def resolve_alert(
    alert_id: int,
    payload: AlertResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> AlertOut:
    """
    Endpoint cập nhật trạng thái xử lý cảnh báo sự cố:
    - Ghi nhận trạng thái mới (RESOLVED hoặc DISMISSED).
    - Lưu vết người xử lý (`resolved_by = current_user.id`) và mốc thời gian hoàn tất (`resolved_at = now()`).
    """
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy cảnh báo có ID {alert_id}.",
        )

    alert.status = payload.status
    alert.resolved_by = current_user.id
    alert.resolved_at = func.now()

    db.commit()
    db.refresh(alert)

    return alert
