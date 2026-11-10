"""RFID adapter for the shared parking workflow."""
import asyncio
import logging
from fastapi import HTTPException

from app.schemas.alpr_schema import CaptureRequest
from app.services.alpr_service import alpr_service
from app.services.parking_flow import create_entry, prepare_exit, record_entry_ocr
from app.services.feedback_service import send_feedback
from app.models.device import Device

logger = logging.getLogger(__name__)


class ParkingEngine:
    def _call_alpr_capture(self, lane: str, session_id: str | None = None):
        try:
            result = asyncio.run(alpr_service.capture(
                CaptureRequest(lane=lane, session_id=session_id)))
            return result
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
                obj = create_entry(db, device_id, uid, None)
                result = self._call_alpr_capture("IN", obj.session_code)
                if result:
                    obj = record_entry_ocr(db, obj.id,
                        result.plate_number or getattr(result, "suggested_plate", None),
                        result.image_url, result.status == "success" and
                        not result.needs_confirmation)
                if obj.status.value == "ENTRY_PENDING":
                    send_feedback(db.query(Device).filter_by(id=device_id).one(), gate,
                                  "PLATE_REVIEW", payload.get("eventId"))
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
            result = self._call_alpr_capture("OUT", obj.session_code)
            obj = prepare_exit(db, obj.id, result.plate_number if result else None,
                               result.image_url if result else None,
                               needs_confirmation=not result or result.needs_confirmation,
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
