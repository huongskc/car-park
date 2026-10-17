from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.card import Card
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import CardType, UserRole
from app.schemas.card_schema import CardCreate, CardStatusUpdate, CardOut

router = APIRouter(
    prefix="/cards",
    tags=["Cards Management"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.get(
    "",
    response_model=list[CardOut],
    summary="Lấy danh sách thẻ RFID (OPERATOR & ADMIN)",
)
def list_cards(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    card_type: Optional[CardType] = Query(default=None, description="Lọc theo loại thẻ"),
    is_active: Optional[bool] = Query(default=None, description="Lọc theo trạng thái kích hoạt"),
    search: Optional[str] = Query(default=None, description="Tìm kiếm theo UID, tên chủ xe hoặc biển số xe"),
    db: Session = Depends(get_db),
) -> list[CardOut]:
    """
    Endpoint quản trị danh sách thẻ RFID:
    - Hỗ trợ phân trang qua `skip` và `limit`.
    - Lọc theo loại thẻ (`card_type`) và trạng thái kích hoạt (`is_active`).
    - Tìm kiếm linh hoạt trên `uid`, `owner_name` và `registered_plate`.
    """
    query = db.query(Card)

    if card_type is not None:
        query = query.filter(Card.card_type == card_type)

    if is_active is not None:
        query = query.filter(Card.is_active == is_active)

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Card.uid.ilike(search_pattern),
                Card.owner_name.ilike(search_pattern),
                Card.registered_plate.ilike(search_pattern),
            )
        )

    return query.order_by(Card.created_at.desc()).offset(skip).limit(limit).all()


@router.post(
    "",
    response_model=CardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký thẻ RFID mới vào hệ thống (OPERATOR & ADMIN)",
)
def create_card(
    payload: CardCreate,
    db: Session = Depends(get_db),
) -> CardOut:
    """
    Endpoint đăng ký thẻ RFID mới:
    - Kiểm tra mã UID vật lý chưa tồn tại trong hệ thống.
    - Mặc định trạng thái kích hoạt (`is_active = True`).
    """
    existing_card = db.query(Card).filter(Card.uid == payload.uid).first()
    if existing_card:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mã UID thẻ RFID '{payload.uid}' đã tồn tại trong hệ thống.",
        )

    new_card = Card(
        uid=payload.uid,
        card_type=payload.card_type,
        owner_name=payload.owner_name,
        registered_plate=payload.registered_plate,
        is_active=True,
    )
    db.add(new_card)
    db.commit()
    db.refresh(new_card)

    return new_card


@router.patch(
    "/{uid}/status",
    response_model=CardOut,
    summary="Cập nhật trạng thái kích hoạt (Khóa / Mở khóa) thẻ RFID (OPERATOR & ADMIN)",
)
def update_card_status(
    uid: str,
    payload: CardStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> CardOut:
    """
    Endpoint khóa hoặc kích hoạt lại thẻ RFID:
    - Chuyển `is_active = False` khi thẻ bị mất hoặc tạm ngừng sử dụng.
    - Chuyển `is_active = True` để mở khóa.
    - Tự động ghi nhận Audit Log.
    """
    card = db.query(Card).filter(Card.uid == uid).first()
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy thẻ RFID có mã UID '{uid}'.",
        )

    card.is_active = payload.is_active

    # Ghi nhận Audit Log
    status_str = "KÍCH HOẠT" if payload.is_active else "KHÓA"
    audit = AuditLog(
        user_id=current_user.id,
        action="CARD_STATUS_UPDATE",
        resource_type="cards",
        resource_id=uid,
        details=f"Cập nhật trạng thái thẻ RFID {uid}: {status_str} (is_active={payload.is_active})",
    )
    db.add(audit)

    db.commit()
    db.refresh(card)

    return card
