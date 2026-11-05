import asyncio
import concurrent.futures
import logging
import re
import secrets
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import (
    CardType,
    CommandAction,
    GateId,
    DeviceStatus,
    PaymentMethod,
    PaymentStatus,
    SessionStatus,
    SlotStatus,
    AlertType,
    AlertSeverity,
    AlertStatus,
)
from app.models.device import Device
from app.models.card import Card
from app.models.slot import Slot
from app.models.session import ParkingSession
from app.models.payment import Payment
from app.models.tariff import Tariff
from app.models.alert import Alert
from app.schemas.command_schema import CommandSend
from app.schemas.alpr_schema import CaptureRequest
from app.services.command_service import command_service
from app.services.alpr_service import alpr_service
from app.services.websocket_manager import websocket_manager

logger = logging.getLogger("smart_parking.parking_engine")


def normalize_plate(plate: Optional[str]) -> str:
    """Chuẩn hóa biển số xe: viết hoa, loại bỏ khoảng trắng và các ký tự đặc biệt."""
    if not plate:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper().strip())


def _safe_run_async(coro):
    """
    Thực thi coroutine bất đồng bộ một cách an toàn cả khi đang chạy trong
    luồng đồng bộ (MQTT thread) lẫn luồng có sẵn event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result(timeout=settings.ALPR_TIMEOUT_SEC + 5.0)


class ParkingEngine:
    """
    Core Parking Engine — Bộ não điều phối tự động toàn bộ quy trình gửi xe:
    - Tiếp nhận sự kiện quẹt thẻ RFID từ đầu đọc RC522 (kênh MQTT).
    - Phân định cổng vào (GATE_IN) và cổng ra (GATE_OUT).
    - Gọi nhận diện thị giác máy tính ALPR (AI Edge Camera).
    - Đối soát an ninh biển số, chống quẹt đúp, kiểm tra sức chứa bãi đỗ.
    - Tự động phát lệnh điều khiển Barrier đóng/mở qua MQTT.
    - Đẩy sự kiện thời gian thực (Realtime Event Broadcasting) qua WebSockets.
    """

    def _call_alpr_capture(self, lane: str, session_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Gọi ALPR Edge Service chụp ảnh và đọc biển số.
        Trả về (plate_number, image_url).
        Nếu camera/AI service lỗi, trả về (None, None).
        """
        target_lane = "IN" if lane.upper() in ["IN", "ENTRY", "GATE_IN"] else "OUT"
        try:
            req = CaptureRequest(lane=target_lane, session_id=session_id)
            result = _safe_run_async(alpr_service.capture(req))
            plate = result.plate_number if result.status == "success" else None
            image_url = result.image_url
            return plate, image_url
        except Exception as e:
            logger.warning(f"[Parking Engine] Lỗi kết nối hoặc xử lý ALPR (lane={target_lane}): {e}")
            return None, None

    def process_rfid_event(self, device_id: str, payload: dict, db: Session) -> Optional[ParkingSession]:
        """
        Hook chính tiếp nhận sự kiện quẹt thẻ RFID được đăng ký vào iot_listener.
        Được gọi tự động mỗi khi có bản tin quẹt thẻ hợp lệ gửi lên từ ESP32.
        """
        card_uid = payload.get("cardUid", payload.get("card_uid"))
        raw_gate = str(payload.get("gate", payload.get("gate_id", "IN"))).upper().strip()

        if not card_uid:
            logger.warning(f"[Parking Engine] Bỏ qua sự kiện RFID thiếu card_uid từ thiết bị '{device_id}'")
            return None

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device or device.status != DeviceStatus.ACTIVATED:
            logger.warning(f"[Parking Engine] Thiết bị '{device_id}' không hợp lệ hoặc chưa kích hoạt")
            return None

        card = db.query(Card).filter(Card.uid == card_uid).first()
        if not card or not card.is_active:
            logger.warning(f"[Parking Engine] Thẻ '{card_uid}' không tồn tại hoặc đã bị khóa")
            return None

        # Phân định cổng vào vs cổng ra
        if any(x in raw_gate for x in ["OUT", "EXIT", "GATE_OUT"]):
            return self._handle_exit_swipe(device=device, card=card, payload=payload, db=db)
        else:
            return self._handle_entry_swipe(device=device, card=card, payload=payload, db=db)

    def _handle_entry_swipe(
        self, device: Device, card: Card, payload: dict, db: Session
    ) -> Optional[ParkingSession]:
        """
        Xử lý sự kiện quẹt thẻ tại cổng vào (GATE_IN):
        1. Chống quẹt đúp (Double Entry Prevention).
        2. Kiểm tra sức chứa bãi đỗ (Capacity Check).
        3. Chụp ảnh & nhận diện biển số lúc vào qua ALPR.
        4. Khởi tạo phiên gửi xe ACTIVE (slot_id = None).
        5. Phát lệnh MQTT mở Barrier cổng vào.
        6. Phát sự kiện WebSockets CAR_ENTERED & BARRIER_STATUS.
        """
        logger.info(f"[Parking Engine] Bắt đầu xử lý quẹt thẻ VÀO: Thẻ '{card.uid}' tại '{device.id}'")

        # 1. Chống quẹt đúp: Thẻ không được phép đang có phiên ACTIVE hoặc PENDING_REVIEW
        active_session = (
            db.query(ParkingSession)
            .filter(
                ParkingSession.card_uid == card.uid,
                ParkingSession.status.in_([SessionStatus.ACTIVE, SessionStatus.PENDING_REVIEW]),
            )
            .first()
        )
        if active_session:
            warn_msg = (
                f"Phát hiện quẹt đúp thẻ: Thẻ UID '{card.uid}' đang có phiên gửi xe hoạt động "
                f"(#{active_session.session_code}, trạng thái: {active_session.status.value}) "
                f"nhưng tiếp tục quẹt vào cổng."
            )
            logger.warning(f"[Parking Engine] {warn_msg}")
            alert = Alert(
                alert_type=AlertType.SYSTEM_ERROR,
                severity=AlertSeverity.WARNING,
                message=warn_msg,
                device_id=device.id,
                session_id=active_session.id,
                status=AlertStatus.NEW,
            )
            db.add(alert)
            db.commit()
            websocket_manager.broadcast_sync(
                "NEW_ALERT",
                {"alert_type": AlertType.SYSTEM_ERROR.value, "severity": "WARNING", "message": warn_msg},
            )
            return None

        # 2. Kiểm tra sức chứa bãi đỗ: Nếu toàn bộ ô đỗ đều bận thì từ chối
        empty_count = db.query(func.count(Slot.id)).filter(Slot.status == SlotStatus.EMPTY).scalar() or 0
        if empty_count == 0:
            full_msg = f"Bãi đỗ xe đã đầy (0 ô trống khả dụng). Từ chối tạo phiên cho thẻ UID '{card.uid}'."
            logger.warning(f"[Parking Engine] {full_msg}")
            alert = Alert(
                alert_type=AlertType.SYSTEM_ERROR,
                severity=AlertSeverity.WARNING,
                message=full_msg,
                device_id=device.id,
                status=AlertStatus.NEW,
            )
            db.add(alert)
            db.commit()
            websocket_manager.broadcast_sync(
                "NEW_ALERT",
                {"alert_type": AlertType.SYSTEM_ERROR.value, "severity": "WARNING", "message": full_msg},
            )
            return None

        # 3. Gọi ALPR chụp ảnh nhận diện biển số lúc vào
        entry_plate, entry_image_url = self._call_alpr_capture("IN")
        if not entry_plate:
            warn_plate = f"Camera cổng vào không nhận diện được biển số cho xe quẹt thẻ '{card.uid}'."
            logger.warning(f"[Parking Engine] {warn_plate}")
            alert = Alert(
                alert_type=AlertType.SYSTEM_ERROR,
                severity=AlertSeverity.WARNING,
                message=warn_plate,
                device_id=device.id,
                status=AlertStatus.NEW,
            )
            db.add(alert)

        # 4. Xác định biểu phí áp dụng
        tariff = db.query(Tariff).filter(Tariff.is_active == True).first()
        if not tariff:
            tariff = db.query(Tariff).first()
        tariff_id = tariff.id if tariff else 1
        base_fee = tariff.flat_rate if tariff else settings.PARKING_FLAT_FEE
        fee_amount = 0.00 if card.card_type == CardType.MONTHLY else base_fee

        # 5. Khởi tạo phiên gửi xe mới (slot_id = None theo thỏa thuận Grill-Me)
        session_code = f"SES_{secrets.token_hex(5).upper()}"
        new_session = ParkingSession(
            session_code=session_code,
            card_uid=card.uid,
            slot_id=None,
            tariff_id=tariff_id,
            status=SessionStatus.ACTIVE,
            entry_plate=entry_plate,
            entry_image_url=entry_image_url,
            fee_amount=fee_amount,
            is_paid=False,
        )
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        logger.info(f"[Parking Engine] Đã tạo phiên xe vào #{session_code} (Biển số: {entry_plate or 'N/A'})")

        # 6. Phát lệnh mở Barrier cổng vào
        try:
            command_payload = CommandSend(
                gate_id=GateId.GATE_IN,
                action=CommandAction.OPEN,
                reason=f"Xe quẹt thẻ vào hợp lệ (Phiên: #{session_code})",
            )
            command_service.send_command(
                device_id=device.id,
                payload=command_payload,
                current_user=None,
                db=db,
            )
            logger.info(f"[Parking Engine] Đã phát lệnh mở Barrier GATE_IN tại '{device.id}'")
        except Exception as cmd_err:
            logger.error(f"[Parking Engine] Lỗi phát lệnh mở Barrier cổng vào: {cmd_err}")

        # 7. Phát sự kiện WebSockets thời gian thực
        websocket_manager.broadcast_sync(
            "CAR_ENTERED",
            {
                "session_code": new_session.session_code,
                "card_uid": card.uid,
                "entry_plate": entry_plate,
                "entry_time": datetime.now().isoformat(),
            },
        )
        websocket_manager.broadcast_sync(
            "BARRIER_STATUS",
            {
                "device_id": device.id,
                "gate_id": GateId.GATE_IN.value,
                "action": CommandAction.OPEN.value,
                "status": "SENT",
            },
        )

        return new_session

    def _handle_exit_swipe(
        self, device: Device, card: Card, payload: dict, db: Session
    ) -> Optional[ParkingSession]:
        """
        Xử lý sự kiện quẹt thẻ tại cổng ra (GATE_OUT):
        1. Tìm phiên xe ACTIVE của thẻ.
        2. Chụp ảnh & nhận diện biển số lúc ra qua ALPR.
        3. Đối soát biển số:
           - Khớp 100%: Tự động thu phí (20.000đ / 0đ thẻ tháng), hoàn tất phiên COMPLETED, mở Barrier ra.
           - Lệch biển số hoặc thiếu biển số: Đổi sang PENDING_REVIEW, giữ Barrier đóng, sinh cảnh báo CRITICAL.
        4. Phát sự kiện WebSockets CAR_EXITED / NEW_ALERT.
        """
        logger.info(f"[Parking Engine] Bắt đầu xử lý quẹt thẻ RA: Thẻ '{card.uid}' tại '{device.id}'")

        # 1. Tìm phiên gửi xe đang hoạt động
        session_obj = (
            db.query(ParkingSession)
            .filter(
                ParkingSession.card_uid == card.uid,
                ParkingSession.status == SessionStatus.ACTIVE,
            )
            .first()
        )
        if not session_obj:
            warn_msg = f"Phát hiện quẹt thẻ ra nhưng thẻ UID '{card.uid}' không có phiên gửi xe hoạt động."
            logger.warning(f"[Parking Engine] {warn_msg}")
            alert = Alert(
                alert_type=AlertType.SYSTEM_ERROR,
                severity=AlertSeverity.WARNING,
                message=warn_msg,
                device_id=device.id,
                status=AlertStatus.NEW,
            )
            db.add(alert)
            db.commit()
            websocket_manager.broadcast_sync(
                "NEW_ALERT",
                {"alert_type": AlertType.SYSTEM_ERROR.value, "severity": "WARNING", "message": warn_msg},
            )
            return None

        # 2. Gọi ALPR chụp ảnh biển số lối ra
        exit_plate, exit_image_url = self._call_alpr_capture("OUT", session_id=session_obj.session_code)
        session_obj.exit_plate = exit_plate
        session_obj.exit_image_url = exit_image_url
        session_obj.exit_time = func.now()

        entry_norm = normalize_plate(session_obj.entry_plate)
        exit_norm = normalize_plate(exit_plate)

        # 3. Đối soát an ninh biển số vào và ra
        is_plate_matched = bool(entry_norm and exit_norm and entry_norm == exit_norm)

        # Trường hợp 1: Biển số khớp 100% -> Tự động thanh toán và mở Barrier ra
        if is_plate_matched:
            fee_amount = 0.00 if card.card_type == CardType.MONTHLY else float(session_obj.fee_amount or settings.PARKING_FLAT_FEE)

            # Ghi nhận bản ghi thanh toán
            payment = Payment(
                session_id=session_obj.id,
                amount=fee_amount,
                payment_method=PaymentMethod.CASH,
                payment_status=PaymentStatus.PAID,
                transaction_code=f"TXN_{secrets.token_hex(6).upper()}",
                paid_at=func.now(),
            )
            db.add(payment)

            # Cập nhật hoàn tất phiên xe
            session_obj.status = SessionStatus.COMPLETED
            session_obj.is_paid = True
            session_obj.fee_amount = fee_amount

            # Nếu phiên có gán ô đỗ cụ thể, giải phóng ô về EMPTY
            if session_obj.slot_id:
                slot = db.query(Slot).filter(Slot.id == session_obj.slot_id).first()
                if slot and slot.status != SlotStatus.MAINTENANCE:
                    slot.status = SlotStatus.EMPTY
                    websocket_manager.broadcast_sync(
                        "SLOT_STATUS_CHANGED",
                        {"slot_id": slot.id, "status": SlotStatus.EMPTY.value},
                    )

            db.commit()
            db.refresh(session_obj)
            logger.info(f"[Parking Engine] Xe ra đối soát thành công #{session_obj.session_code}. Thu phí: {fee_amount}đ")

            # Mở Barrier cổng ra
            try:
                command_payload = CommandSend(
                    gate_id=GateId.GATE_OUT,
                    action=CommandAction.OPEN,
                    reason=f"Xe đối soát biển số khớp ({exit_plate}), hoàn tất thanh toán",
                )
                command_service.send_command(
                    device_id=device.id,
                    payload=command_payload,
                    current_user=None,
                    db=db,
                )
                logger.info(f"[Parking Engine] Đã phát lệnh mở Barrier GATE_OUT tại '{device.id}'")
            except Exception as cmd_err:
                logger.error(f"[Parking Engine] Lỗi phát lệnh mở Barrier cổng ra: {cmd_err}")

            # Broadcast WebSockets
            websocket_manager.broadcast_sync(
                "CAR_EXITED",
                {
                    "session_code": session_obj.session_code,
                    "card_uid": card.uid,
                    "exit_plate": exit_plate,
                    "fee_amount": fee_amount,
                    "exit_time": datetime.now().isoformat(),
                },
            )
            websocket_manager.broadcast_sync(
                "BARRIER_STATUS",
                {
                    "device_id": device.id,
                    "gate_id": GateId.GATE_OUT.value,
                    "action": CommandAction.OPEN.value,
                    "status": "SENT",
                },
            )
            return session_obj

        # Trường hợp 2: Lệch biển số hoặc thiếu biển số -> Giữ Barrier đóng, chuyển PENDING_REVIEW
        else:
            reason = (
                f"Lệch hoặc thiếu biển số đối soát (Biển vào: '{session_obj.entry_plate or 'Trống'}' "
                f"vs Biển ra: '{exit_plate or 'Trống'}')."
            )
            session_obj.status = SessionStatus.PENDING_REVIEW
            session_obj.review_reason = reason

            alert = Alert(
                alert_type=AlertType.PLATE_MISMATCH,
                severity=AlertSeverity.CRITICAL,
                message=(
                    f"CẢNH BÁO AN NINH: {reason} Tại cổng ra {device.id} cho thẻ UID '{card.uid}'. "
                    f"Barrier giữ đóng, nhân viên vui lòng duyệt tay trên Web Dashboard."
                ),
                device_id=device.id,
                session_id=session_obj.id,
                status=AlertStatus.NEW,
            )
            db.add(alert)
            db.commit()
            db.refresh(session_obj)
            logger.warning(f"[Parking Engine] {reason} Phiên chuyển sang PENDING_REVIEW. Barrier giữ đóng.")

            websocket_manager.broadcast_sync(
                "NEW_ALERT",
                {
                    "alert_type": AlertType.PLATE_MISMATCH.value,
                    "severity": "CRITICAL",
                    "message": reason,
                    "session_code": session_obj.session_code,
                },
            )
            return session_obj


parking_engine = ParkingEngine()
