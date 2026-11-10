"""Payment reconciliation for staff; payments are recorded by checkout only."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.payment import Payment
from app.schemas.payment_schema import PaymentOut

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("", response_model=list[PaymentOut])
def list_payments(day: date | None = None, skip: int = Query(0, ge=0),
                  limit: int = Query(100, ge=1, le=200),
                  db: Session = Depends(get_db),
                  _user=Depends(require_role(UserRole.OPERATOR))):
    query = db.query(Payment)
    if day:
        start = datetime.combine(day, time.min)
        query = query.filter(Payment.paid_at >= start,
                             Payment.paid_at < start + timedelta(days=1))
    return query.order_by(Payment.paid_at.desc(), Payment.id.desc()).offset(skip).limit(limit).all()
