"""Business transitions shared by RFID events and operator actions."""
import logging
import re
import secrets
from datetime import date, datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.card import Card
from app.models.command import Command
from app.models.device import Device
from app.models.enums import (
    CardType, CommandAction, CommandStatus, DeviceStatus, GateId,
    PaymentMethod, PaymentStatus, SessionStatus, SlotStatus,
)
from app.models.payment import Payment
from app.models.session import ParkingSession
from app.models.slot import Slot
from app.models.tariff import Tariff
from app.schemas.command_schema import CommandSend
from app.services.command_service import command_service

logger = logging.getLogger(__name__)
OPEN_STATES = (
    SessionStatus.ENTRY_PENDING, SessionStatus.ACTIVE, SessionStatus.EXIT_READY,
    SessionStatus.PENDING_REVIEW, SessionStatus.PAID_WAITING_EXIT,
)


def normalize_plate(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _session(db: Session, session_id: int) -> ParkingSession:
    obj = db.query(ParkingSession).filter_by(id=session_id).with_for_update().first()
    if not obj:
        raise HTTPException(404, "Không tìm thấy phiên gửi xe.")
    return obj


def _audit(db: Session, action: str, obj: ParkingSession, user=None, details: str = "") -> None:
    db.add(AuditLog(user_id=user.id if user else None, action=action,
                    resource_type="parking_sessions", resource_id=str(obj.id), details=details))


def _send_gate(db: Session, obj: ParkingSession, gate: GateId, user=None) -> None:
    """Keep the session pending if publish fails; the failed command is persisted."""
    try:
        command_service.send_command(
            obj.device_id,
            CommandSend(gate_id=gate, action=CommandAction.OPEN,
                        reason=f"Phiên {obj.session_code}"),
            user, db, session_id=obj.id,
        )
    except HTTPException as exc:
        logger.warning("Gate publish deferred for session %s: %s", obj.session_code, exc.detail)


def _monthly_valid(card: Card) -> bool:
    today = date.today()
    return bool(card.card_type == CardType.MONTHLY and card.valid_from and card.valid_until
                and card.valid_from <= today <= card.valid_until)


def create_entry(db: Session, device_id: str, card_uid: str, plate: str | None,
                 image_url: str | None = None, user=None) -> ParkingSession:
    card = db.query(Card).filter_by(uid=card_uid).with_for_update().first()
    if not card or not card.is_active:
        raise HTTPException(400, "Thẻ không tồn tại hoặc đã bị khóa.")
    if db.query(ParkingSession.id).filter(ParkingSession.card_uid == card_uid,
                                          ParkingSession.status.in_(OPEN_STATES)).first():
        raise HTTPException(409, "Thẻ đang có lượt gửi xe chưa kết thúc.")
    device = db.query(Device).filter_by(id=device_id).first()
    if not device or device.status != DeviceStatus.ACTIVATED:
        raise HTTPException(400, "Thiết bị cổng chưa kích hoạt.")
    occupied_by_session = db.query(ParkingSession.id).filter(
        ParkingSession.slot_id == Slot.id,
        ParkingSession.status.in_(OPEN_STATES),
    ).exists()
    slot = (db.query(Slot).filter(Slot.site_id == device.site_id,
                                  Slot.status == SlotStatus.EMPTY,
                                  ~occupied_by_session)
            .order_by(Slot.id).with_for_update(skip_locked=True).first())
    if not slot:
        raise HTTPException(409, "Không còn ô trống khả dụng.")
    tariff = (db.query(Tariff).filter(Tariff.is_active.is_(True),
                                      Tariff.fee_type == "FLAT",
                                      Tariff.effective_from <= datetime.now())
              .order_by(Tariff.effective_from.desc(), Tariff.id.desc()).first())
    if not tariff:
        raise HTTPException(400, "Chưa có biểu phí đồng giá đang hoạt động.")
    normalized = normalize_plate(plate)
    if card.card_type == CardType.MONTHLY and not card.registered_plate:
        raise HTTPException(400, "Thẻ tháng thiếu biển đăng ký.")
    fee = 0 if _monthly_valid(card) else tariff.flat_rate
    obj = ParkingSession(
        session_code=f"SES_{secrets.token_hex(5).upper()}", card_uid=card_uid,
        slot_id=slot.id, device_id=device_id, tariff_id=tariff.id,
        status=SessionStatus.ENTRY_PENDING, entry_time=None,
        entry_plate=plate.strip().upper() if normalized else None,
        entry_image_url=image_url, fee_amount=fee, is_paid=False,
    )
    slot.status = SlotStatus.RESERVED
    db.add(obj)
    db.flush()
    _audit(db, "ENTRY_CREATED", obj, user, f"Ô được cấp: {slot.id}")
    db.commit()
    db.refresh(obj)
    if normalized and not (card.card_type == CardType.MONTHLY
                           and normalize_plate(card.registered_plate) != normalized):
        confirm_entry_plate(db, obj.id, plate, user=user, note="OCR" if not user else "Nhập tại cổng")
    return obj


def confirm_entry_plate(db: Session, session_id: int, plate: str, user=None,
                        note: str = "Nhân viên xác nhận") -> ParkingSession:
    obj = _session(db, session_id)
    if obj.status != SessionStatus.ENTRY_PENDING:
        raise HTTPException(409, "Lượt vào không còn chờ xác nhận.")
    normalized = normalize_plate(plate)
    if not normalized:
        raise HTTPException(400, "Cần nhập biển số hợp lệ.")
    card = db.query(Card).filter_by(uid=obj.card_uid).first()
    if card.card_type == CardType.MONTHLY and normalize_plate(card.registered_plate) != normalized:
        raise HTTPException(409, "Biển số khác biển đăng ký của thẻ tháng.")
    previous = obj.entry_plate
    obj.entry_plate = plate.strip().upper()
    _audit(db, "ENTRY_PLATE_CONFIRMED", obj, user,
           f"{previous or 'trống'} -> {obj.entry_plate}; {note}")
    db.commit()
    pending = db.query(Command).filter_by(session_id=obj.id, gate_id=GateId.GATE_IN,
                                            action=CommandAction.OPEN, status=CommandStatus.SENT).first()
    if not pending:
        _send_gate(db, obj, GateId.GATE_IN, user)
    db.refresh(obj)
    return obj


def prepare_exit(db: Session, session_id: int, plate: str | None,
                 image_url: str | None = None, user=None) -> ParkingSession:
    obj = _session(db, session_id)
    if obj.status != SessionStatus.ACTIVE:
        raise HTTPException(409, "Chỉ chuẩn bị lượt ra cho phiên đang gửi xe.")
    obj.exit_plate = plate.strip().upper() if normalize_plate(plate) else None
    obj.exit_image_url = image_url
    obj.status = (SessionStatus.EXIT_READY if normalize_plate(obj.entry_plate)
                  and normalize_plate(obj.entry_plate) == normalize_plate(obj.exit_plate)
                  else SessionStatus.PENDING_REVIEW)
    _audit(db, "EXIT_PLATE_CHECKED", obj, user, f"Kết quả: {obj.status.value}")
    db.commit()
    db.refresh(obj)
    if obj.status == SessionStatus.EXIT_READY and float(obj.fee_amount) == 0:
        return checkout(db, obj.id, PaymentMethod.SYSTEM, user=None)
    return obj


def approve_exit(db: Session, session_id: int, approved: bool, note: str,
                 corrected_plate: str | None, user) -> ParkingSession:
    obj = _session(db, session_id)
    if obj.status != SessionStatus.PENDING_REVIEW:
        raise HTTPException(409, "Phiên không ở trạng thái chờ duyệt.")
    if approved:
        if not normalize_plate(corrected_plate):
            raise HTTPException(400, "Cần nhập biển ra nhân viên đã xác nhận.")
        obj.exit_plate = corrected_plate.strip().upper()
        obj.status = SessionStatus.EXIT_READY
    obj.reviewed_by = user.id
    obj.review_reason = note if approved else f"[REJECTED] {note}"
    _audit(db, "EXIT_REVIEW_APPROVED" if approved else "EXIT_REVIEW_REJECTED",
           obj, user, f"{obj.review_reason}; biển xác nhận: {obj.exit_plate or 'trống'}")
    db.commit()
    db.refresh(obj)
    if approved and float(obj.fee_amount) == 0:
        return checkout(db, obj.id, PaymentMethod.SYSTEM, user=None)
    return obj


def checkout(db: Session, session_id: int, method: PaymentMethod, user=None) -> ParkingSession:
    obj = _session(db, session_id)
    if obj.status != SessionStatus.EXIT_READY:
        raise HTTPException(409, "Phiên chưa sẵn sàng thu phí/ra cổng.")
    if db.query(Payment.id).filter_by(session_id=obj.id).first():
        raise HTTPException(409, "Phiên đã có thanh toán; không thu lần hai.")
    if float(obj.fee_amount) > 0 and (user is None or method == PaymentMethod.SYSTEM):
        raise HTTPException(400, "Cần nhân viên xác nhận thanh toán.")
    db.add(Payment(session_id=obj.id, amount=obj.fee_amount, payment_method=method,
                   payment_status=PaymentStatus.PAID,
                   transaction_code=f"TXN_{secrets.token_hex(6).upper()}",
                   operator_id=user.id if user else None))
    obj.is_paid = True
    obj.status = SessionStatus.PAID_WAITING_EXIT
    _audit(db, "PAYMENT_CONFIRMED", obj, user, f"{method.value}: {obj.fee_amount}")
    db.commit()
    _send_gate(db, obj, GateId.GATE_OUT, user)
    db.refresh(obj)
    return obj


def retry_gate(db: Session, session_id: int, user=None) -> ParkingSession:
    obj = _session(db, session_id)
    if obj.status not in (SessionStatus.ENTRY_PENDING, SessionStatus.PAID_WAITING_EXIT):
        raise HTTPException(409, "Phiên không chờ mở cổng.")
    gate = GateId.GATE_IN if obj.status == SessionStatus.ENTRY_PENDING else GateId.GATE_OUT
    if gate == GateId.GATE_IN and not obj.entry_plate:
        raise HTTPException(409, "Cần xác nhận biển vào trước.")
    if gate == GateId.GATE_IN:
        card = db.query(Card).filter_by(uid=obj.card_uid).first()
        if card.card_type == CardType.MONTHLY and normalize_plate(card.registered_plate) != normalize_plate(obj.entry_plate):
            raise HTTPException(409, "Cần sửa biển OCR cho khớp biển đăng ký trước khi mở cổng.")
    if db.query(Command.id).filter_by(session_id=obj.id, gate_id=gate,
                                      action=CommandAction.OPEN, status=CommandStatus.SENT).first():
        raise HTTPException(409, "Lệnh mở cổng trước vẫn đang chờ ACK.")
    _send_gate(db, obj, gate, user)
    db.refresh(obj)
    return obj


def cancel_entry(db: Session, session_id: int, reason: str, user,
                 confirmed_departure: bool = False) -> ParkingSession:
    reason = reason.strip()
    if len(reason) < 3:
        raise HTTPException(400, "Cần nhập lý do hủy lượt vào.")
    obj = _session(db, session_id)
    if obj.status != SessionStatus.ENTRY_PENDING:
        raise HTTPException(409, "Chỉ hủy lượt vào đang chờ.")
    if db.query(Command.id).filter_by(session_id=obj.id, gate_id=GateId.GATE_IN,
                                      status=CommandStatus.SENT).first():
        raise HTTPException(409, "Chờ lệnh cổng kết thúc trước khi hủy.")
    slot = db.query(Slot).filter_by(id=obj.slot_id).with_for_update().first()
    if slot and slot.status == SlotStatus.OCCUPIED and not confirmed_departure:
        raise HTTPException(409, "Ô đang báo có xe; cần xác nhận xe đã rời trước khi hủy.")
    if slot and slot.status == SlotStatus.RESERVED:
        slot.status = SlotStatus.EMPTY
    obj.status = SessionStatus.CANCELLED
    _audit(db, "ENTRY_CANCELLED", obj, user,
           f"{reason}; xác nhận xe đã rời: {'có' if confirmed_departure else 'không'}")
    db.commit()
    db.refresh(obj)
    return obj


def on_gate_ack(db: Session, command: Command) -> None:
    """Called before ACK transaction commits, only for a newly ACKED command."""
    if not command.session_id or command.action != CommandAction.OPEN:
        return
    obj = _session(db, command.session_id)
    if command.gate_id == GateId.GATE_IN and obj.status == SessionStatus.ENTRY_PENDING:
        obj.status = SessionStatus.ACTIVE
        obj.entry_time = datetime.now()
    elif command.gate_id == GateId.GATE_OUT and obj.status == SessionStatus.PAID_WAITING_EXIT:
        obj.status = SessionStatus.COMPLETED
        obj.exit_time = datetime.now()
    else:
        return
    _audit(db, "GATE_ACK_COMPLETED", obj, details=command.id)
