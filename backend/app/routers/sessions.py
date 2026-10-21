import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.core.dependencies import require_role
from app.models.enums import (
    UserRole,
    SessionStatus,
    CardType,
    SlotStatus,
    PaymentStatus,
    AlertType,
    AlertSeverity,
    AlertStatus,
)
from app.models.user import User
from app.models.card import Card
from app.models.slot import Slot
from app.models.tariff import Tariff
from app.models.session import ParkingSession
from app.models.payment import Payment
from app.models.audit import AuditLog
from app.models.alert import Alert
from app.schemas.session_schema import (
    SessionCreate,
    SessionResolvePlate,
    SessionCancel,
    SessionCheckout,
    SessionOut,
)

router = APIRouter(
    prefix="/sessions",
    tags=["Sessions Management"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


def _lock_session_and_slot(db: Session, session_id: int) -> Optional[ParkingSession]:
    """Lock the slot before the session, matching manual and telemetry updates."""
    slot_id = db.query(ParkingSession.slot_id).filter(ParkingSession.id == session_id).scalar()
    if slot_id:
        db.query(Slot).filter(Slot.id == slot_id).with_for_update().first()
    return db.query(ParkingSession).filter(ParkingSession.id == session_id).with_for_update().first()


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
    # 1. Kiểm tra thẻ RFID (sử dụng with_for_update() khóa bản ghi tránh race condition quẹt trùng thẻ)
    card = db.query(Card).filter(Card.uid == payload.card_uid).with_for_update().first()
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

    # 3. Kiểm tra ô đỗ (nếu có chỉ định, sử dụng with_for_update() khóa hàng tránh Race Condition cấp phát ô)
    if payload.slot_id:
        slot = db.query(Slot).filter(Slot.id == payload.slot_id).with_for_update().first()
        if not slot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy ô đỗ '{payload.slot_id}'.",
            )
        if slot.status == SlotStatus.MAINTENANCE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ô đỗ '{payload.slot_id}' đang trong trạng thái bảo trì kỹ thuật, không thể gán xe.",
            )
        if slot.status != SlotStatus.EMPTY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ô đỗ '{payload.slot_id}' hiện không khả dụng (Trạng thái: {slot.status.value}).",
            )

        existing_slot_session = (
            db.query(ParkingSession)
            .filter(
                ParkingSession.slot_id == payload.slot_id,
                ParkingSession.status.in_([SessionStatus.ACTIVE, SessionStatus.PENDING_REVIEW]),
            )
            .first()
        )
        if existing_slot_session:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ô đỗ '{payload.slot_id}' hiện đang có phiên gửi xe hoạt động (#{existing_slot_session.session_code}).",
            )
        slot.status = SlotStatus.OCCUPIED

    # 4. Lấy biểu phí áp dụng
    tariff = db.query(Tariff).filter(Tariff.is_active == True).first()
    if not tariff:
        tariff = db.query(Tariff).first()
    if not tariff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không tìm thấy biểu phí nào được thiết lập trong hệ thống. Vui lòng cấu hình biểu phí trước khi khởi tạo phiên gửi xe.",
        )
    tariff_id = tariff.id
    base_fee = tariff.flat_rate
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
    session_obj = (
        db.query(ParkingSession)
        .filter(ParkingSession.id == session_id)
        .with_for_update()
        .first()
    )
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên gửi xe với ID {session_id}.",
        )

    if session_obj.status != SessionStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chỉ có thể xử lý ngoại lệ cho phiên xe đang ở trạng thái chờ duyệt (PENDING_REVIEW). Trạng thái hiện tại: {session_obj.status.value}.",
        )

    session_obj.reviewed_by = current_user.id
    session_obj.review_reason = payload.note

    if payload.approved:
        session_obj.status = SessionStatus.ACTIVE
        session_obj.reviewed_by = current_user.id
        session_obj.review_reason = payload.note
        decision_str = "Chấp thuận duyệt cho xe ra (chuyển sang ACTIVE)"
    else:
        # Giữ nguyên PENDING_REVIEW và giữ ô OCCUPIED vì phương tiện chưa được xác minh đã rời bãi
        session_obj.review_reason = f"[REJECTED] {payload.note}"
        decision_str = "Từ chối duyệt cho xe ra (giữ nguyên PENDING_REVIEW và ô OCCUPIED)"

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
    "/{session_id}/cancel",
    response_model=SessionOut,
    summary="Hủy phiên gửi xe ngoại lệ và giải phóng ô đỗ (Chỉ dành cho ADMIN)",
)
def cancel_session_by_admin(
    session_id: int,
    payload: SessionCancel,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> SessionOut:
    """
    Lối xử lý quản trị dành riêng cho Quản trị viên (ADMIN):
    - Cho phép hủy phiên gửi xe ngoại lệ không thể checkout thông thường (ví dụ: xe đã dời ngoài thực địa).
    - Chỉ áp dụng cho phiên đang ở trạng thái PENDING_REVIEW và chưa thanh toán (is_paid = False).
    - Bắt buộc xác nhận phương tiện đã thực tế rời bãi an toàn (confirmed_exit = True).
    - Tự động giải phóng ô đỗ liên quan về EMPTY.
    - Ghi vết kiểm toán bắt buộc vào audit_logs.
    """
    session_obj = _lock_session_and_slot(db, session_id)
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy phiên gửi xe với ID {session_id}.",
        )

    if not payload.confirmed_exit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bắt buộc phải xác nhận phương tiện đã thực tế rời khỏi bãi đỗ trước khi hủy phiên.",
        )

    if session_obj.is_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể hủy phiên gửi xe đã hoàn tất thanh toán.",
        )

    if session_obj.status != SessionStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chỉ có thể hủy phiên đang ở trạng thái chờ duyệt (PENDING_REVIEW). Trạng thái hiện tại: {session_obj.status.value}.",
        )

    session_obj.status = SessionStatus.CANCELLED
    session_obj.reviewed_by = current_user.id
    session_obj.review_reason = f"[ADMIN CANCELLED] {payload.reason}"
    session_obj.exit_time = func.now()

    # Giải phóng ô đỗ về EMPTY
    if session_obj.slot_id:
        slot = db.query(Slot).filter(Slot.id == session_obj.slot_id).first()
        if slot and slot.status == SlotStatus.OCCUPIED:
            slot.status = SlotStatus.EMPTY

    # Ghi nhận Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="ADMIN_CANCEL_SESSION",
        resource_type="parking_sessions",
        resource_id=str(session_obj.id),
        details=f"Quản trị viên hủy phiên #{session_obj.session_code}. Lý do: {payload.reason}. Xác nhận xe đã rời bãi: {payload.confirmed_exit}.",
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
    session_obj = _lock_session_and_slot(db, session_id)
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

    if session_obj.status == SessionStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiên gửi xe đã bị hủy (CANCELLED), không thể thực hiện thanh toán checkout.",
        )

    if session_obj.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chỉ có thể checkout phiên gửi xe đang hoạt động (ACTIVE). Trạng thái hiện tại: {session_obj.status.value}.",
        )

    # Kiểm tra lệch biển số xe lúc ra (Plate Mismatch Detection):
    # Trường hợp 1: Phiên đã qua nhân viên duyệt ngoại lệ (reviewed_by is not None)
    if session_obj.reviewed_by is not None:
        if payload.exit_plate is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phiên gửi xe đã qua duyệt ngoại lệ, bắt buộc phải cung cấp biển số ra (exit_plate) để xác nhận.",
            )
        # Biển số gửi lên lúc checkout bắt buộc phải khớp với đúng biển số ra đã được duyệt
        target_plate = session_obj.exit_plate or session_obj.entry_plate
        if payload.exit_plate != target_plate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Biển số ra '{payload.exit_plate}' không khớp với biển số đã được nhân viên vận hành "
                    f"phê duyệt ngoại lệ ('{target_plate}')."
                ),
            )
    # Trường hợp 2: Phiên chưa từng được nhân viên duyệt ngoại lệ (reviewed_by is None)
    else:
        # Nếu phiên có ghi nhận biển số vào (entry_plate): bắt buộc phải gửi exit_plate để đối soát
        if session_obj.entry_plate:
            if payload.exit_plate is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Phiên gửi xe #{session_obj.session_code} có ghi nhận biển số lúc vào ('{session_obj.entry_plate}'). "
                        f"Bắt buộc phải cung cấp biển số xe lúc ra (exit_plate) để đối soát an ninh."
                    ),
                )
            if payload.exit_plate != session_obj.entry_plate:
                session_obj.exit_plate = payload.exit_plate
                if payload.exit_image_url is not None:
                    session_obj.exit_image_url = payload.exit_image_url
                session_obj.status = SessionStatus.PENDING_REVIEW

                alert = Alert(
                    alert_type=AlertType.PLATE_MISMATCH,
                    severity=AlertSeverity.WARNING,
                    message=(
                        f"Phát hiện lệch biển số xe tại làn ra: Biển vào '{session_obj.entry_plate}' "
                        f"!= Biển ra '{payload.exit_plate}' (Phiên #{session_obj.session_code}). "
                        f"Chờ nhân viên vận hành duyệt ngoại lệ."
                    ),
                    session_id=session_obj.id,
                    status=AlertStatus.NEW,
                )
                db.add(alert)

                audit = AuditLog(
                    user_id=current_user.id,
                    action="DETECT_PLATE_MISMATCH",
                    resource_type="parking_sessions",
                    resource_id=str(session_obj.id),
                    details=f"Lệch biển số xe: Vào '{session_obj.entry_plate}', Ra '{payload.exit_plate}'. Chuyển sang PENDING_REVIEW.",
                )
                db.add(audit)
                db.commit()

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Biển số ra '{payload.exit_plate}' không khớp với biển số vào '{session_obj.entry_plate}'. "
                        f"Phiên gửi xe #{session_obj.session_code} đã được chuyển sang trạng thái chờ duyệt (PENDING_REVIEW)."
                    ),
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

    try:
        db.commit()
        db.refresh(session_obj)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Giao dịch thanh toán cho phiên gửi xe này đã được xử lý bởi một yêu cầu đồng thời khác.",
        )

    return session_obj
