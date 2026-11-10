"""Best-effort LCD feedback for rejected RFID events; no barrier action."""
import logging
from datetime import datetime

from app.config import settings
from app.services.mqtt_service import mqtt_service

logger = logging.getLogger(__name__)

MESSAGES = {
    "INVALID_CARD": "THE KHONG HOP LE",
    "CARD_IN_USE": "THE DANG SU DUNG",
    "LOT_FULL": "BAI DA DAY",
    "EXIT_NOT_FOUND": "KHONG CO LUOT GUI",
    "PLATE_REVIEW": "CHO KIEM TRA BIEN",
}


def send_feedback(device, gate: str, code: str, event_id: str | None = None) -> None:
    if code not in MESSAGES:
        return
    payload = {"eventId": event_id, "gate": gate, "code": code,
               "message": MESSAGES[code], "issuedAt": datetime.now().astimezone().isoformat()}
    topic = f"{settings.MQTT_BASE_TOPIC}/{device.site_id}/{device.id}/feedback"
    try:
        mqtt_service.publish(topic, payload, qos=1, retain=False)
    except Exception:
        logger.exception("Không gửi được phản hồi LCD tới %s", device.id)
