"""RFID adapter for the shared parking workflow."""
import asyncio
import logging
from fastapi import HTTPException

from app.schemas.alpr_schema import CaptureRequest
from app.services.alpr_service import alpr_service
from app.services.parking_flow import broadcast_session, create_entry, prepare_exit, record_entry_ocr
from app.services.feedback_service import send_feedback
from app.models.device import Device

logger = logging.getLogger(__name__)


class ParkingEngine:
    def _call_alpr_capture(self, lane: str, session_id: str | None = None, on_snapshot=None):
        try:
            async def run():
                payload = CaptureRequest(lane=lane, session_id=session_id)
                image_url, image_bytes = await alpr_service.snapshot(payload)
                if on_snapshot:
                    on_snapshot(image_url)
                result = await alpr_service.recognize(payload, image_bytes, "image/jpeg", lane_crop=True)
                result.image_url = image_url
                result.image_id = image_url.rsplit("/", 1)[-1]
                return result
            return asyncio.run(run())
        except Exception as exc:
            logger.warning("ALPR %s unavailable: %s", lane, exc)
            return None

    def process_rfid_event(self, device_id: str, payload: dict, db):
        uid = payload.get("cardUid", payload.get("card_uid"))
        gate = str(payload.get("gate", payload.get("gate_id", ""))).upper().strip()
        if not uid or gate not in ("GATE_IN", "GATE_OUT"):
            logger.warning("Invalid RFID event gate or card from %s", device_id)
            return None
        try:
            if gate == "GATE_IN":
                from app.models.card import Card
                from app.models.session import ParkingSession
                from app.models.slot import Slot
                from app.models.enums import SlotStatus
                from app.services.parking_flow import OPEN_STATES

                device = db.query(Device).filter_by(id=device_id).first()
                if not device:
                    return None
                card = db.query(Card).filter_by(uid=uid).first()
                if not card or not card.is_active:
                    send_feedback(device, gate, "INVALID_CARD", payload.get("eventId"))
                    logger.warning("RFID card %s invalid or inactive", uid)
                    return None

                # Pre-check: Does card already have an open session?
                if db.query(ParkingSession.id).filter(
                    ParkingSession.card_uid == uid,
                    ParkingSession.status.in_(OPEN_STATES)
                ).first():
                    send_feedback(device, gate, "CARD_IN_USE", payload.get("eventId"))
                    logger.warning("RFID workflow rejected: Thẻ %s đang có lượt gửi xe chưa kết thúc.", uid)
                    return None

                # Pre-check: Are there empty slots?
                occupied_by_session = db.query(ParkingSession.id).filter(
                    ParkingSession.slot_id == Slot.id,
                    ParkingSession.status.in_(OPEN_STATES)
                ).exists()
                has_empty = db.query(Slot.id).filter(
                    Slot.site_id == device.site_id,
                    Slot.status == SlotStatus.EMPTY,
                    ~occupied_by_session
                ).first()
                if not has_empty:
                    send_feedback(device, gate, "LOT_FULL", payload.get("eventId"))
                    logger.warning("RFID workflow rejected: Không còn ô trống khả dụng.")
                    return None

                obj = create_entry(db, device_id, uid, plate=None)
                def store_entry_snapshot(image_url):
                    nonlocal obj
                    obj = record_entry_ocr(db, obj.id, None, image_url)

                result = self._call_alpr_capture("IN", obj.session_code, store_entry_snapshot)
                img_url = result.image_url if result else None
                if result:
                    suggested = result.plate_number or getattr(result, "suggested_plate", None)
                    db.refresh(obj)
                    if obj.status.value == "ENTRY_PENDING" and obj.entry_image_url in (None, img_url):
                        obj = record_entry_ocr(db, obj.id, suggested, img_url)

                if obj.status.value == "ENTRY_PENDING":
                    send_feedback(device, gate, "PLATE_REVIEW", payload.get("eventId"))
                return obj
            from app.models.session import ParkingSession
            from app.models.enums import SessionStatus
            obj = (db.query(ParkingSession).filter(
                ParkingSession.card_uid == uid,
                ParkingSession.status == SessionStatus.ACTIVE,
            ).first())
            if not obj or obj.device_id != device_id:
                logger.warning("No active session for RFID exit card %s", uid)
                send_feedback(db.query(Device).filter_by(id=device_id).one(), gate,
                              "EXIT_NOT_FOUND", payload.get("eventId"))
                return None
            snapshot_url = None

            def store_exit_snapshot(image_url):
                nonlocal snapshot_url
                snapshot_url = image_url
                obj.exit_image_url = image_url
                obj.exit_ocr_plate = None
                db.commit()
                broadcast_session(obj)

            result = self._call_alpr_capture("OUT", obj.session_code, store_exit_snapshot)
            db.refresh(obj)
            if obj.status != SessionStatus.ACTIVE:
                return obj
            img_url = snapshot_url or (result.image_url if result else None)
            if snapshot_url and obj.exit_image_url != snapshot_url:
                return obj
            obj = prepare_exit(db, obj.id, result.plate_number if result else None,
                               img_url,
                               needs_confirmation=True,
                               ocr_plate=(result.plate_number or getattr(result, "suggested_plate", None)) if result else None)
            if obj.status.value == "PENDING_REVIEW":
                send_feedback(db.query(Device).filter_by(id=device_id).one(), gate,
                              "PLATE_REVIEW", payload.get("eventId"))
            return obj
        except HTTPException as exc:
            db.rollback()
            message = str(exc.detail)
            code = ("LOT_FULL" if "Không còn ô" in message else
                    "CARD_IN_USE" if "Thẻ đang có" in message else "INVALID_CARD")
            device = db.query(Device).filter_by(id=device_id).first()
            if device:
                send_feedback(device, gate, code, payload.get("eventId"))
            logger.warning("RFID workflow rejected: %s", message)
            return None
        except Exception:
            db.rollback()
            logger.exception("RFID workflow failed for device %s", device_id)
            return None


parking_engine = ParkingEngine()
