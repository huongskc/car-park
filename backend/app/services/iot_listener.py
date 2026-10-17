import logging
from datetime import datetime
from typing import Any, Callable, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.enums import DeviceStatus, SlotStatus, AlertType, AlertSeverity, AlertStatus
from app.models.device import Device, DeviceConfig
from app.models.slot import Slot
from app.models.card import Card
from app.models.telemetry import TelemetryLog
from app.models.alert import Alert
from app.services.mqtt_service import mqtt_service, MQTTService

logger = logging.getLogger("smart_parking.iot_listener")


def parse_topic_info(topic: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Bóc tách thông tin định danh từ topic MQTT.
    Hỗ trợ 2 định dạng:
    1. Chuẩn phân vùng 3 cấp: parking/{site_id}/{device_id}/{msg_type...}
       Ví dụ: parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry -> ("PTIT_ZONE_1", "ESP32_GATE_01", "telemetry")
       Ví dụ: parking/PTIT_ZONE_1/ESP32_GATE_01/events/rfid -> ("PTIT_ZONE_1", "ESP32_GATE_01", "events/rfid")
    2. Rút gọn 2 cấp: parking/{device_id}/{msg_type...} hoặc parking/devices/{device_id}/{msg_type...}
       Ví dụ: parking/ESP32_GATE_01/status -> (None, "ESP32_GATE_01", "status")
       Ví dụ: parking/devices/ESP32_GATE_01/status -> (None, "ESP32_GATE_01", "status")
    """
    parts = topic.strip("/").split("/")
    if len(parts) < 3:
        return None, None, None

    # parking/devices/{device_id}/...
    if parts[1] == "devices" and len(parts) >= 4:
        device_id = parts[2]
        msg_type = "/".join(parts[3:])
        return None, device_id, msg_type

    # parking/{site_id}/{device_id}/... (4 phần trở lên nếu có sub-type như events/rfid)
    if len(parts) >= 4 and parts[0] == settings.MQTT_BASE_TOPIC:
        site_id = parts[1]
        device_id = parts[2]
        msg_type = "/".join(parts[3:])
        return site_id, device_id, msg_type

    # parking/{device_id}/{msg_type} (3 phần)
    if len(parts) == 3 and parts[0] == settings.MQTT_BASE_TOPIC:
        device_id = parts[1]
        msg_type = parts[2]
        return None, device_id, msg_type

    return None, None, None


class IoTListener:
    """
    Engine lắng nghe và đồng bộ dữ liệu IoT thời gian thực từ Mosquitto Broker vào MySQL.
    Xử lý:
    - Nhịp tim / Trạng thái thiết bị (Heartbeat / Status)
    - Đo đạc cảm biến siêu âm & trạng thái ô đỗ (Telemetry & Slots)
    - Sự kiện quẹt thẻ RFID tại Barrier (RFID Events)
    - Tự động phát hiện vi phạm và sinh Cảnh báo (Alerts)
    """

    def __init__(self) -> None:
        self._rfid_hooks: list[Callable[[str, dict, Session], None]] = []

    def register_rfid_hook(self, hook: Callable[[str, dict, Session], None]) -> None:
        """Đăng ký hook mở rộng xử lý sự kiện quẹt thẻ (kết nối Core Engine ở Milestone 5)."""
        if hook not in self._rfid_hooks:
            self._rfid_hooks.append(hook)

    # ----------------------------------------------------
    # 1. XỬ LÝ BẢN TIN TRẠNG THÁI & NHỊP TIM (HEARTBEAT)
    # ----------------------------------------------------
    def handle_status(self, topic: str, payload: Any) -> None:
        """
        Tiếp nhận bản tin nhịp tim (Heartbeat) và thông tin phần mềm vi điều khiển.
        Cập nhật: last_heartbeat_at, is_online, fw_version, ip_address.
        """
        _, device_id, _ = parse_topic_info(topic)
        if not device_id:
            logger.warning(f"[IoT Listener] Không thể xác định device_id từ topic: {topic}")
            return

        if not isinstance(payload, dict):
            logger.warning(f"[IoT Listener] Payload status không phải JSON dict: {payload}")
            return

        db: Session = SessionLocal()
        try:
            device = db.query(Device).filter(Device.id == device_id).first()
            if not device:
                logger.warning(f"[IoT Listener] Nhận status từ thiết bị chưa đăng ký: '{device_id}'")
                return

            device.last_heartbeat_at = func.now()
            device.is_online = payload.get("isOnline", payload.get("is_online", True))

            if "fwVersion" in payload:
                device.fw_version = payload["fwVersion"]
            elif "fw_version" in payload:
                device.fw_version = payload["fw_version"]

            if "ipAddress" in payload:
                device.ip_address = payload["ipAddress"]
            elif "ip_address" in payload:
                device.ip_address = payload["ip_address"]

            db.commit()
            logger.info(f"[IoT Listener] Đã cập nhật Heartbeat cho thiết bị '{device_id}' (online={device.is_online})")
        except Exception as e:
            db.rollback()
            logger.error(f"[IoT Listener] Lỗi cập nhật status cho '{device_id}': {e}", exc_info=True)
        finally:
            db.close()

    # ----------------------------------------------------
    # 2. XỬ LÝ BẢN TIN TELEMETRY CẢM BIẾN & ĐỒNG BỘ Ô ĐỖ
    # ----------------------------------------------------
    def handle_telemetry(self, topic: str, payload: Any) -> None:
        """
        Tiếp nhận bản tin đo khoảng cách từ cảm biến siêu âm.
        Quy trình:
        1. Kiểm tra trạng thái vòng đời thiết bị: Bắt buộc ACTIVATED.
        2. So khớp khoảng cách với ngưỡng cấu hình (distance_threshold_cm).
        3. Lưu bản ghi vào bảng telemetry_logs.
        4. Đồng bộ trạng thái ô đỗ (EMPTY <-> OCCUPIED).
        5. Bảo lưu trạng thái nếu ô đang bảo trì (MAINTENANCE) và sinh cảnh báo nếu có xe đỗ trái phép.
        """
        _, device_id, _ = parse_topic_info(topic)
        if not device_id:
            logger.warning(f"[IoT Listener] Không thể xác định device_id từ topic: {topic}")
            return

        if not isinstance(payload, dict):
            logger.warning(f"[IoT Listener] Payload telemetry không phải JSON dict: {payload}")
            return

        slot_id = payload.get("slotId", payload.get("slot_id"))
        raw_distance = payload.get("distanceCm", payload.get("distance_cm"))
        if slot_id is None or raw_distance is None:
            logger.warning(f"[IoT Listener] Bản tin telemetry thiếu slotId hoặc distanceCm: {payload}")
            return

        try:
            distance_cm = round(float(raw_distance), 2)
        except (ValueError, TypeError):
            logger.warning(f"[IoT Listener] Giá trị khoảng cách không hợp lệ: {raw_distance}")
            return

        db: Session = SessionLocal()
        try:
            device = db.query(Device).filter(Device.id == device_id).first()
            if not device:
                logger.warning(f"[IoT Listener] Bỏ qua telemetry từ thiết bị chưa đăng ký: '{device_id}'")
                return

            # Kiểm soát vòng đời thiết bị nghiêm ngặt (Grill-Me đã chốt)
            if device.status != DeviceStatus.ACTIVATED:
                alert_msg = (
                    f"Thiết bị '{device.id}' gửi bản tin Telemetry khi chưa kích hoạt vận hành "
                    f"(Trạng thái hiện tại: {device.status.value})."
                )
                alert = Alert(
                    alert_type=AlertType.SYSTEM_ERROR,
                    severity=AlertSeverity.WARNING,
                    message=alert_msg,
                    device_id=device.id,
                    status=AlertStatus.NEW,
                )
                db.add(alert)
                db.commit()
                logger.warning(f"[IoT Listener] {alert_msg}")
                return

            # Lấy ngưỡng khoảng cách từ cấu hình thiết bị (hoặc fallback mặc định)
            config = db.query(DeviceConfig).filter(DeviceConfig.device_id == device_id).first()
            threshold = float(config.distance_threshold_cm) if config else settings.DISTANCE_THRESHOLD_CM

            # Xe hiện diện nếu khoảng cách <= ngưỡng
            is_occupied = (distance_cm <= threshold)

            # Phân tích mốc thời gian lấy mẫu
            sampled_at_str = payload.get("sampledAt", payload.get("sampled_at"))
            sampled_at = datetime.now()
            if sampled_at_str:
                try:
                    sampled_at = datetime.fromisoformat(str(sampled_at_str).replace("Z", "+00:00"))
                except Exception:
                    pass

            # 1. Ghi log Telemetry vào bảng telemetry_logs
            telemetry_entry = TelemetryLog(
                device_id=device.id,
                slot_id=str(slot_id),
                distance_cm=distance_cm,
                is_occupied=is_occupied,
                boot_id=payload.get("bootId", payload.get("boot_id")),
                seq=payload.get("seq"),
                sampled_at=sampled_at,
            )
            db.add(telemetry_entry)

            # 2. Cập nhật trạng thái ô đỗ trong bảng slots
            slot = db.query(Slot).filter(Slot.id == slot_id).first()
            if slot:
                slot.last_distance_cm = distance_cm
                slot.last_updated_at = func.now()

                # Kiểm tra trạng thái bảo trì quản trị (Grill-Me đã chốt)
                if slot.status == SlotStatus.MAINTENANCE:
                    if is_occupied:
                        maint_alert = Alert(
                            alert_type=AlertType.SYSTEM_ERROR,
                            severity=AlertSeverity.WARNING,
                            message=f"Phát hiện phương tiện đỗ trái phép tại ô đang bảo trì '{slot.id}' (Khoảng cách: {distance_cm} cm).",
                            device_id=device.id,
                            status=AlertStatus.NEW,
                        )
                        db.add(maint_alert)
                        logger.warning(f"[IoT Listener] Vi phạm đỗ xe tại ô bảo trì '{slot.id}'!")
                    # Giữ nguyên trạng thái MAINTENANCE
                elif slot.status == SlotStatus.RESERVED:
                    # Giữ nguyên trạng thái RESERVED
                    pass
                else:
                    # Tự động đồng bộ EMPTY <-> OCCUPIED
                    new_status = SlotStatus.OCCUPIED if is_occupied else SlotStatus.EMPTY
                    if slot.status != new_status:
                        logger.info(f"[IoT Listener] Ô đỗ '{slot.id}' đổi trạng thái: {slot.status.value} -> {new_status.value}")
                        slot.status = new_status
            else:
                logger.warning(f"[IoT Listener] Không tìm thấy ô đỗ '{slot_id}' trong CSDL.")

            # Cập nhật mốc nhận tín hiệu của thiết bị
            device.last_heartbeat_at = func.now()
            device.is_online = True

            db.commit()
            logger.debug(f"[IoT Listener] Đã lưu telemetry cho ô '{slot_id}': {distance_cm}cm (occupied={is_occupied})")
        except Exception as e:
            db.rollback()
            logger.error(f"[IoT Listener] Lỗi xử lý telemetry cho '{device_id}': {e}", exc_info=True)
        finally:
            db.close()

    # ----------------------------------------------------
    # 3. XỬ LÝ SỰ KIỆN QUẸT THẺ RFID TẠI BARRIER
    # ----------------------------------------------------
    def handle_rfid(self, topic: str, payload: Any) -> None:
        """
        Tiếp nhận sự kiện quẹt thẻ RFID từ đầu đọc RC522 gửi qua ESP32.
        Quy trình:
        1. Kiểm tra trạng thái vòng đời thiết bị: Bắt buộc ACTIVATED.
        2. Đối soát thẻ trong CSDL (bảng cards):
           - Không tồn tại hoặc is_active = False -> Sinh cảnh báo UNAUTHORIZED_CARD.
        3. Kích hoạt hook callback xử lý phiên gửi xe (Milestone 5).
        """
        _, device_id, _ = parse_topic_info(topic)
        if not device_id:
            logger.warning(f"[IoT Listener] Không thể xác định device_id từ topic: {topic}")
            return

        if not isinstance(payload, dict):
            logger.warning(f"[IoT Listener] Payload rfid không phải JSON dict: {payload}")
            return

        card_uid = payload.get("cardUid", payload.get("card_uid"))
        if not card_uid:
            logger.warning(f"[IoT Listener] Bản tin RFID thiếu cardUid: {payload}")
            return

        gate_id = payload.get("gate", payload.get("gate_id", "ENTRY"))

        db: Session = SessionLocal()
        try:
            device = db.query(Device).filter(Device.id == device_id).first()
            if not device:
                logger.warning(f"[IoT Listener] Bỏ qua RFID từ thiết bị chưa đăng ký: '{device_id}'")
                return

            if device.status != DeviceStatus.ACTIVATED:
                alert_msg = (
                    f"Thiết bị '{device.id}' gửi sự kiện RFID khi chưa kích hoạt vận hành "
                    f"(Trạng thái: {device.status.value})."
                )
                alert = Alert(
                    alert_type=AlertType.SYSTEM_ERROR,
                    severity=AlertSeverity.WARNING,
                    message=alert_msg,
                    device_id=device.id,
                    status=AlertStatus.NEW,
                )
                db.add(alert)
                db.commit()
                logger.warning(f"[IoT Listener] {alert_msg}")
                return

            # Kiểm tra thẻ trong cơ sở dữ liệu
            card = db.query(Card).filter(Card.uid == card_uid).first()
            if not card:
                alert = Alert(
                    alert_type=AlertType.UNAUTHORIZED_CARD,
                    severity=AlertSeverity.WARNING,
                    message=f"Phát hiện quẹt thẻ không hợp lệ (không tồn tại trong CSDL): UID '{card_uid}' tại cổng {gate_id}.",
                    device_id=device.id,
                    status=AlertStatus.NEW,
                )
                db.add(alert)
                db.commit()
                logger.warning(f"[IoT Listener] Cảnh báo: Thẻ '{card_uid}' không tồn tại trong hệ thống!")
                return

            if not card.is_active:
                alert = Alert(
                    alert_type=AlertType.UNAUTHORIZED_CARD,
                    severity=AlertSeverity.WARNING,
                    message=f"Phát hiện quẹt thẻ đã bị khóa: UID '{card_uid}' (Chủ thẻ: {card.owner_name or 'N/A'}) tại cổng {gate_id}.",
                    device_id=device.id,
                    status=AlertStatus.NEW,
                )
                db.add(alert)
                db.commit()
                logger.warning(f"[IoT Listener] Cảnh báo: Thẻ '{card_uid}' đã bị khóa nhưng vẫn quẹt qua cổng!")
                return

            logger.info(f"[IoT Listener] Thẻ hợp lệ: UID '{card_uid}' ({card.card_type.value}) tại cổng {gate_id}")

            # Kích hoạt các hook mở rộng cho Milestone 5 (Parking Session & Barrier Engine)
            for hook in self._rfid_hooks:
                try:
                    hook(device.id, payload, db)
                except Exception as hook_err:
                    logger.error(f"[IoT Listener] Lỗi thực thi RFID hook: {hook_err}", exc_info=True)

            device.last_heartbeat_at = func.now()
            device.is_online = True
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[IoT Listener] Lỗi xử lý sự kiện RFID cho '{device_id}': {e}", exc_info=True)
        finally:
            db.close()


# Khởi tạo singleton listener instance
iot_listener = IoTListener()


def setup_iot_listeners(client: MQTTService = mqtt_service) -> None:
    """
    Đăng ký toàn bộ các topic bản tin IoT vào MQTT Service.
    Hỗ trợ cả chuẩn phân vùng 3 cấp và chuẩn rút gọn 2 cấp.
    """
    logger.info("[IoT Listener] Đang đăng ký các router lắng nghe bản tin IoT...")

    # 1. Đăng ký nhận Status / Heartbeat
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/+/status", iot_listener.handle_status)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/status", iot_listener.handle_status)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/devices/+/status", iot_listener.handle_status)

    # 2. Đăng ký nhận Telemetry cảm biến khoảng cách
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/+/telemetry", iot_listener.handle_telemetry)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/telemetry", iot_listener.handle_telemetry)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/devices/+/telemetry", iot_listener.handle_telemetry)

    # 3. Đăng ký nhận Sự kiện quẹt thẻ RFID
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/+/events/rfid", iot_listener.handle_rfid)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/+/events/rfid", iot_listener.handle_rfid)
    client.subscribe(f"{settings.MQTT_BASE_TOPIC}/devices/+/events/rfid", iot_listener.handle_rfid)

    logger.info("[IoT Listener] Đã đăng ký thành công toàn bộ IoT Listeners vào MQTTService.")
