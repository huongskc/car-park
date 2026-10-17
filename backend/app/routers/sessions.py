import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import UserRole, SessionStatus, CardType, SlotStatus, PaymentStatus
from app.models.user import User
from app.models.card import Card
from app.models.slot import Slot
from app.models.tariff import Tariff
from app.models.session import ParkingSession
from app.models.payment import Payment
from app.models.audit import AuditLog
from app.schemas.session_schema import (
    SessionCreate,
    SessionResolvePlate,
    SessionCheckout,
    SessionOut,
)

router = APIRouter(
    prefix="/sessions",
    tags=["Sessions Management"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.get(
    "",
    response_model=list[SessionOut],
    summary="Lấy danh sách phiên gửi xe (OPERATOR & ADMIN)",
)
def list_sessions(
    skip: int = Query(default=0, ge=0, description="Số lượng bản ghi bỏ qua"),
    limit: int = Query(default=50, ge=1, le=100, description="Số lượng bản ghi tối đa"),
    status_filter: Optional[SessionStatus] = Query(default=None, alias="status", description="Lọc theo trạng thái phiên"),
    search: Optional[str] = Query(default=None, description="Tìm kiếm theo mã phiên, UID thẻ, hoặc biển số xe vào/ra"),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    """
    Endpoint tra cứu danh sách phiên gửi xe:
    - Hỗ trợ phân trang và lọc theo trạng thái (ACTIVE, COMPLETED, PENDING_REVIEW, CANCELLED).
    - Tìm kiếm gần đúng linh hoạt trên mã phiên, mã UID thẻ RFID, biển số lúc vào và lúc ra.
    - Sắp xếp giảm dần theo thời gian vào (mới nhất lên đầu).
    """
    query = db.query(ParkingSession)

    if status_filter is not None:
        query = query.filter(ParkingSession.status == status_filter)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                ParkingSession.session_code.ilike(pattern),
                ParkingSession.card_uid.ilike(pattern),
                ParkingSession.entry_plate.ilike(pattern),
                ParkingSession.exit_plate.ilike(pattern),
            )
        )

    return query.order_by(ParkingSession.entry_time.desc()).offset(skip).limit(limit).all()


@router.post(
    "",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Khởi tạo phiên gửi xe mới (OPERATOR & ADMIN)",
)
def create_session(
    payload: SessionCreate,
    db: Session = Depends(get_db),
) -> SessionOut:
    """
    Endpoint khởi tạo phiên gửi xe:
    - Kiểm tra thẻ RFID hợp lệ và đang kích hoạt.
    - Chống quẹt thẻ đúp (thẻ đang có phiên chưa hoàn tất).
    - Tự động gán biểu phí và cập nhật trạng thái ô đỗ sang OCCUPIED (nếu có ô đỗ).
    """
    # 1. Kiểm tra thẻ RFID
    card = db.query(Card).filter(Card.uid == payload.card_uid).first()
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thẻ RFID có mã UID '{payload.card_uid}' không tồn tại trong hệ thống.",
        )
    if not card.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Thẻ RFID '{payload.card_uid}' đang bị khóa hoặc báo mất.",
        )

    # 2. Kiểm tra phiên đang hoạt động của thẻ
    active_session = (
        db.query(ParkingSession)
        .filter(
            ParkingSession.card_uid == payload.card_uid,
            ParkingSession.status.in_([SessionStatus.ACTIVE, SessionStatus.PENDING_REVIEW]),
        )
        .first()
    )
    if active_session:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Thẻ RFID '{payload.card_uid}' đang nằm trong phiên gửi xe chưa kết thúc (#{active_session.session_code}).",
        )

    # 3. Kiểm tra ô đỗ (nếu có chỉ định)
    if payload.slot_id:
        slot = db.query(Slot).filter(Slot.id == payload.slot_id).first()
        if not slot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy ô đỗ '{payload.slot_id}'.",
            )
        slot.status = SlotStatus.OCCUPIED

    # 4. Lấy biểu phí áp dụng
    tariff = db.query(Tariff).filter(Tariff.is_active == True).first()
    tariff_id = tariff.id if tariff else 1
    base_fee = tariff.flat_rate if tariff else 20000.00
    fee_amount = 0.00 if card.card_type == CardType.MONTHLY else base_fee

    # 5. Tạo phiên gửi xe mới
    session_code = f"SES_{uuid.uuid4().hex[:10].upper()}"
    new_session = ParkingSession(
        session_code=session_code,
        card_uid=payload.card_uid,
        slot_id=payload.slot_id,
        tariff_id=tariff_id,
        status=SessionStatus.ACTIVE,
        entry_plate=payload.entry_plate,
        entry_image_url=payload.entry_image_url,
        fee_amount=fee_amount,
        is_paid=False,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return new_session


@router.get(
    "/{session_id}",
    response_model=SessionOut,
    summary="Xem chi tiết phiên gửi xe theo ID (OPERATOR & ADMIN)",
)
def get_session_detail(
    session_id: int,
    db: Session = Depends(get_db),
) -> SessionOut:
    """Xem chi tiết đầy đủ thông tin của một phiên gửi xe."""
    session_obj = db.query(ParkingSession).filter(ParkingSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên gửi xe với ID {session_id}.",
        )
    return session_obj


@router.patch(
    "/{session_id}/resolve-plate",
    response_model=SessionOut,
    summary="Duyệt ngoại lệ lệch biển số xe (OPERATOR & ADMIN)",
)
def resolve_plate_mismatch(
    session_id: int,
    payload: SessionResolvePlate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> SessionOut:
    """
    Endpoint xử lý ngoại lệ lệch biển số xe lúc ra:
    - Cập nhật lý do và định danh nhân viên xử lý (`reviewed_by`, `review_reason`).
    - Nếu `approved = True`: chuyển trạng thái về `ACTIVE` để sẵn sàng tiến hành thanh toán và ra cổng.
    - Nếu `approved = False`: chuyển trạng thái về `CANCELLED`.
    - TỰ ĐỘNG tạo bản ghi nhật ký kiểm toán vào bảng `audit_logs` để bảo đảm tính minh bạch.
    """
    session_obj = db.query(ParkingSession).filter(ParkingSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên gửi xe với ID {session_id}.",
        )

    session_obj.reviewed_by = current_user.id
    session_obj.review_reason = payload.note

    if payload.approved:
        session_obj.status = SessionStatus.ACTIVE
        decision_str = "Chấp thuận duyệt cho xe ra"
    else:
        session_obj.status = SessionStatus.CANCELLED
        decision_str = "Từ chối duyệt cho xe ra"
        # Giải phóng ô đỗ về EMPTY nếu bị từ chối
        if session_obj.slot_id:
            slot = db.query(Slot).filter(Slot.id == session_obj.slot_id).first()
            if slot and slot.status == SlotStatus.OCCUPIED:
                slot.status = SlotStatus.EMPTY

    # Tự động ghi nhận Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="RESOLVE_PLATE_MISMATCH",
        resource_type="parking_sessions",
        resource_id=str(session_obj.id),
        details=f"Quyết định: {decision_str}. Lý do: {payload.note}",
    )
    db.add(audit)

    db.commit()
    db.refresh(session_obj)

    return session_obj


@router.post(
    "/{session_id}/checkout",
    response_model=SessionOut,
    summary="Thu phí và kết thúc phiên gửi xe (OPERATOR & ADMIN)",
)
def checkout_session(
    session_id: int,
    payload: SessionCheckout,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OPERATOR)),
) -> SessionOut:
    """
    Endpoint thanh toán và kết thúc phiên gửi xe:
    - Tính phí: 0 VNĐ cho thẻ tháng (`MONTHLY`), 20.000 VNĐ cho khách vãng lai (`VISITOR`).
    - Tạo bản ghi thanh toán vào bảng `payments` (trạng thái `PAID`).
    - Cập nhật phiên xe: `is_paid = True`, `status = COMPLETED`, `exit_time = now()`.
    - Tự động giải phóng ô đỗ liên quan về trạng thái `EMPTY`.
    """
    session_obj = db.query(ParkingSession).filter(ParkingSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên gửi xe với ID {session_id}.",
        )

    if session_obj.status == SessionStatus.COMPLETED or session_obj.is_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiên gửi xe này đã được thanh toán và hoàn tất trước đó.",
        )

    if session_obj.status == SessionStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiên gửi xe đang trong trạng thái chờ duyệt ngoại lệ. Vui lòng duyệt trước khi checkout.",
        )

    # 1. Xác định số tiền thu
    card = db.query(Card).filter(Card.uid == session_obj.card_uid).first()
    if card and card.card_type == CardType.MONTHLY:
        charged_amount = 0.00
    else:
        charged_amount = float(session_obj.fee_amount) if session_obj.fee_amount is not None else 20000.00

    # 2. Tạo bản ghi giao dịch thanh toán
    tx_code = f"TXN_{uuid.uuid4().hex[:10].upper()}"
    payment = Payment(
        session_id=session_obj.id,
        amount=charged_amount,
        payment_method=payload.payment_method,
        payment_status=PaymentStatus.PAID,
        transaction_code=tx_code,
        operator_id=current_user.id,
    )
    db.add(payment)

    # 3. Cập nhật phiên gửi xe
    session_obj.is_paid = True
    session_obj.status = SessionStatus.COMPLETED
    session_obj.exit_time = func.now()
    if payload.exit_plate is not None:
        session_obj.exit_plate = payload.exit_plate
    if payload.exit_image_url is not None:
        session_obj.exit_image_url = payload.exit_image_url

    # 4. Tự động giải phóng ô đỗ về EMPTY
    if session_obj.slot_id:
        slot = db.query(Slot).filter(Slot.id == session_obj.slot_id).first()
        if slot and slot.status == SlotStatus.OCCUPIED:
            slot.status = SlotStatus.EMPTY

    db.commit()
    db.refresh(session_obj)

    return session_obj
