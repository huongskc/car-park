"""Operator APIs for the shared entry and exit workflow."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.dependencies import require_role
from app.database import get_db
from app.models.audit import AuditLog
from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertType, GateId, SessionStatus, UserRole
from app.models.session import ParkingSession
from app.models.user import User
from app.schemas.session_schema import (
    SessionCreate, SessionOut, SessionResolvePlate, SessionCancel,
    SessionCheckout, EntryPlateConfirm, ExitPrepare, EntryCancel, PassageConfirm, ExitCancel,
)
from app.services import parking_flow

router = APIRouter(prefix="/sessions", tags=["Sessions Management"],
                   dependencies=[Depends(require_role(UserRole.OPERATOR))])


@router.get("", response_model=list[SessionOut])
def list_sessions(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  status_filter: Optional[SessionStatus] = Query(None, alias="status"),
                  search: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(ParkingSession)
    if status_filter:
        query = query.filter(ParkingSession.status == status_filter)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(ParkingSession.session_code.ilike(pattern),
                                 ParkingSession.card_uid.ilike(pattern),
                                 ParkingSession.entry_plate.ilike(pattern),
                                 ParkingSession.exit_plate.ilike(pattern)))
    return query.order_by(ParkingSession.created_at.desc(), ParkingSession.id.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.create_entry(db, payload.device_id, payload.card_uid,
                                     payload.entry_plate, payload.entry_image_url, user)


@router.get("/{session_id}", response_model=SessionOut)
def get_session_detail(session_id: int, db: Session = Depends(get_db)):
    obj = db.query(ParkingSession).filter_by(id=session_id).first()
    if not obj:
        raise HTTPException(404, "Không tìm thấy phiên gửi xe.")
    return obj


@router.patch("/{session_id}/entry-plate", response_model=SessionOut)
def confirm_entry_plate(session_id: int, payload: EntryPlateConfirm,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.confirm_entry_plate(db, session_id, payload.plate, user,
                                            payload.note, payload.image_url)


@router.post("/{session_id}/confirm-entry", response_model=SessionOut)
def confirm_entry_passage(session_id: int, payload: PassageConfirm,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_role(UserRole.OPERATOR))):
    if not payload.confirmed_passage:
        raise HTTPException(400, "Cần xác nhận đã thấy xe qua cổng vào.")
    return parking_flow.confirm_passage(db, session_id, GateId.GATE_IN, user, payload.note)


@router.post("/{session_id}/confirm-exit", response_model=SessionOut)
def confirm_exit_passage(session_id: int, payload: PassageConfirm,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role(UserRole.OPERATOR))):
    if not payload.confirmed_passage:
        raise HTTPException(400, "Cần xác nhận đã thấy xe qua cổng ra.")
    return parking_flow.confirm_passage(db, session_id, GateId.GATE_OUT, user, payload.note)


@router.post("/{session_id}/prepare-exit", response_model=SessionOut)
def prepare_exit(session_id: int, payload: ExitPrepare, db: Session = Depends(get_db),
                 user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.prepare_exit(db, session_id, payload.exit_plate,
                                     payload.exit_image_url, user)


@router.patch("/{session_id}/resolve-plate", response_model=SessionOut)
def resolve_plate_mismatch(session_id: int, payload: SessionResolvePlate,
                           db: Session = Depends(get_db),
                           user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.approve_exit(db, session_id, payload.approved, payload.note,
                                     payload.actual_exit_plate, user)


@router.post("/{session_id}/checkout", response_model=SessionOut, status_code=202)
def checkout_session(session_id: int, payload: SessionCheckout,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.checkout(db, session_id, payload.payment_method, user)


@router.post("/{session_id}/retry-gate", response_model=SessionOut)
def retry_gate(session_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.retry_gate(db, session_id, user)


@router.post("/{session_id}/cancel-entry", response_model=SessionOut)
def cancel_entry(session_id: int, payload: EntryCancel,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_role(UserRole.OPERATOR))):
    return parking_flow.cancel_entry(db, session_id, payload.reason, user,
                                     payload.confirmed_departure)


@router.post("/{session_id}/cancel-exit", response_model=SessionOut)
def cancel_exit(session_id: int, payload: ExitCancel, db: Session = Depends(get_db),
                user: User = Depends(require_role(UserRole.OPERATOR))):
    if not payload.confirmed_not_exited:
        raise HTTPException(400, "Cần xác nhận xe chưa qua cổng ra.")
    return parking_flow.cancel_exit(db, session_id, payload.reason, user)


@router.post("/{session_id}/cancel", response_model=SessionOut)
def cancel_session(session_id: int, payload: SessionCancel,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role(UserRole.OPERATOR))):
    """Exceptional physical exit after review; never change paid sessions."""
    if len(payload.reason.strip()) < 3:
        raise HTTPException(400, "Cần nhập lý do hủy phiên.")
    obj = db.query(ParkingSession).filter_by(id=session_id).with_for_update().first()
    if not obj:
        raise HTTPException(404, "Không tìm thấy phiên gửi xe.")
    if obj.status != SessionStatus.PENDING_REVIEW or obj.is_paid or not payload.confirmed_exit:
        raise HTTPException(409, "Chỉ hủy phiên chờ duyệt, chưa thanh toán, khi xe đã rời bãi.")
    obj.status = SessionStatus.CANCELLED
    obj.exit_time = datetime.now()
    obj.reviewed_by = user.id
    obj.review_reason = payload.reason
    for alert in db.query(Alert).filter(
        Alert.session_id == obj.id,
        Alert.alert_type == AlertType.PLATE_MISMATCH,
        Alert.status.in_([AlertStatus.NEW, AlertStatus.ACKNOWLEDGED]),
    ).all():
        alert.status = AlertStatus.RESOLVED
        alert.resolved_by = user.id
        alert.resolved_at = datetime.now()
    db.add(AuditLog(user_id=user.id, action="CANCEL_SESSION", resource_type="parking_sessions",
                    resource_id=str(obj.id), details=payload.reason))
    db.commit()
    db.refresh(obj)
    parking_flow.broadcast_session(obj)
    return obj


cancel_session_by_admin = cancel_session
