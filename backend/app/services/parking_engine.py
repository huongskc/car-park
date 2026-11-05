"""RFID adapter for the shared parking workflow."""
import asyncio
import logging

from app.schemas.alpr_schema import CaptureRequest
from app.services.alpr_service import alpr_service
from app.services.parking_flow import create_entry, prepare_exit

logger = logging.getLogger(__name__)


class ParkingEngine:
    def _call_alpr_capture(self, lane: str, session_id: str | None = None):
        try:
            result = asyncio.run(alpr_service.capture(
                CaptureRequest(lane=lane, session_id=session_id)))
            return (result.plate_number if result.status == "success" else None,
                    result.image_url or None)
        except Exception as exc:
            logger.warning("ALPR %s unavailable: %s", lane, exc)
            return None, None

    def process_rfid_event(self, device_id: str, payload: dict, db):
        uid = payload.get("cardUid", payload.get("card_uid"))
        gate = str(payload.get("gate", payload.get("gate_id", ""))).upper().strip()
        if not uid or gate not in ("GATE_IN", "GATE_OUT"):
            logger.warning("Invalid RFID event gate or card from %s", device_id)
            return None
        try:
            if gate == "GATE_IN":
                plate, image = self._call_alpr_capture("IN")
                return create_entry(db, device_id, uid, plate, image)
            from app.models.session import ParkingSession
            from app.models.enums import SessionStatus
            obj = (db.query(ParkingSession).filter(
                ParkingSession.card_uid == uid,
                ParkingSession.status == SessionStatus.ACTIVE,
            ).first())
            if not obj or obj.device_id != device_id:
                logger.warning("No active session for RFID exit card %s", uid)
                return None
            plate, image = self._call_alpr_capture("OUT", obj.session_code)
            return prepare_exit(db, obj.id, plate, image)
        except Exception:
            db.rollback()
            logger.exception("RFID workflow failed for device %s", device_id)
            return None


parking_engine = ParkingEngine()
