import json
import logging
from typing import Any, Callable, Optional
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.config import settings

logger = logging.getLogger("smart_parking.mqtt")
logging.basicConfig(level=logging.INFO)


class MQTTService:
    """
    Dịch vụ nền tảng MQTT Client (Singleton) kết nối tới Mosquitto Broker.
    Tuân thủ nguyên lý SOLID (SRP & OCP) và Callback API Version 2 của paho-mqtt.
    Đóng vai trò Topic Router / Event Dispatcher cho toàn bộ bản tin IoT.
    """

    def __init__(self) -> None:
        self._client: mqtt.Client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=settings.MQTT_CLIENT_ID,
        )

        # Cấu hình xác thực nếu có
        if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
            self._client.username_pw_set(
                username=settings.MQTT_USERNAME,
                password=settings.MQTT_PASSWORD,
            )

        # Trạng thái kết nối
        self._is_connected: bool = False

        # Danh bạ đăng ký xử lý tin nhắn: {topic_pattern: [callback_fn, ...]}
        self._handlers: dict[str, list[Callable[[str, Any], None]]] = {}

        # Gán các callbacks chuẩn Version 2
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def is_connected(self) -> bool:
        """Kiểm tra trạng thái kết nối tới Mosquitto Broker."""
        return self._is_connected

    @property
    def client(self) -> mqtt.Client:
        """Truy xuất trực tiếp đối tượng paho-mqtt Client khi cần."""
        return self._client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Callback kích hoạt khi thiết lập kết nối tới Broker thành công (hoặc thất bại)."""
        is_success = getattr(reason_code, "value", reason_code) == 0
        if is_success:
            self._is_connected = True
            logger.info(
                f"[MQTT] Kết nối thành công tới Mosquitto Broker tại "
                f"{settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT} (Client ID: {settings.MQTT_CLIENT_ID})"
            )

            # Tự động re-subscribe lại toàn bộ các topic đã đăng ký trước đó
            for topic in self._handlers.keys():
                client.subscribe(topic, qos=1)
                logger.info(f"[MQTT] Đã re-subscribe topic: {topic}")
        else:
            self._is_connected = False
            logger.error(f"[MQTT] Kết nối Broker thất bại. Reason Code: {reason_code}")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Callback kích hoạt khi bị ngắt kết nối khỏi Broker."""
        self._is_connected = False
        logger.warning(
            f"[MQTT] Mất kết nối tới Mosquitto Broker. Reason Code: {reason_code}. "
            f"Paho MQTT Client sẽ tự động thử kết nối lại trong nền."
        )

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        """
        Callback trung tâm tiếp nhận bản tin MQTT và phân phối (Dispatch) tới các handler.
        Hỗ trợ đối khớp wildcard topic ('+' và '#') chuẩn mực của giao thức MQTT.
        """
        topic = message.topic
        raw_payload = message.payload.decode("utf-8", errors="replace")

        # Thử parse JSON payload, nếu thất bại thì giữ nguyên chuỗi thô
        try:
            payload = json.loads(raw_payload)
        except Exception:
            payload = raw_payload

        logger.debug(f"[MQTT IN] Topic: {topic} | Payload: {raw_payload[:120]}")

        # Dispatch tin nhắn tới các handlers khớp với topic
        dispatched = False
        for pattern, callbacks in self._handlers.items():
            if mqtt.topic_matches_sub(pattern, topic):
                for cb in callbacks:
                    try:
                        cb(topic, payload)
                        dispatched = True
                    except Exception as e:
                        logger.error(f"[MQTT ERROR] Lỗi khi thực thi handler cho topic '{topic}': {e}", exc_info=True)

        if not dispatched:
            logger.debug(f"[MQTT] Nhận tin nhắn trên topic '{topic}' nhưng không có handler nào đăng ký.")

    def subscribe(
        self,
        topic: str,
        handler: Callable[[str, Any], None],
        qos: int = 1,
    ) -> None:
        """
        Đăng ký một hàm xử lý (handler) cho một topic pattern cụ thể.
        Ví dụ: `subscribe("parking/+/telemetry", on_telemetry)`
        """
        if topic not in self._handlers:
            self._handlers[topic] = []
        if handler not in self._handlers[topic]:
            self._handlers[topic].append(handler)

        # Nếu client đang online, tiến hành subscribe ngay lập tức lên Broker
        if self._is_connected:
            self._client.subscribe(topic, qos=qos)
            logger.info(f"[MQTT] Đã gửi lệnh subscribe topic: '{topic}' (QoS {qos})")

    def unsubscribe(self, topic: str) -> None:
        """Hủy đăng ký nhận tin nhắn trên một topic."""
        if topic in self._handlers:
            del self._handlers[topic]
        if self._is_connected:
            self._client.unsubscribe(topic)
            logger.info(f"[MQTT] Đã hủy subscribe topic: '{topic}'")

    def publish(
        self,
        topic: str,
        payload: dict | str,
        qos: int = 1,
        retain: bool = False,
    ) -> mqtt.MQTTMessageInfo:
        """
        Gửi bản tin MQTT tới một topic xác định.
        Tự động serialize dict sang JSON UTF-8.
        """
        if isinstance(payload, dict):
            payload_str = json.dumps(payload, ensure_ascii=False)
        else:
            payload_str = str(payload)

        msg_info = self._client.publish(
            topic,
            payload=payload_str,
            qos=qos,
            retain=retain,
        )
        logger.debug(f"[MQTT OUT] Topic: {topic} | QoS: {qos} | Retain: {retain}")
        return msg_info

    def start(self) -> None:
        """
        Khởi động kết nối ngầm (Non-blocking) và luồng mạng nền (loop_start).
        Đảm bảo nếu Broker chưa chạy thì FastAPI vẫn khởi động bình thường không crash.
        """
        logger.info(
            f"[MQTT] Bắt đầu khởi động MQTT Client Service kết nối tới "
            f"{settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}..."
        )
        try:
            self._client.connect_async(
                host=settings.MQTT_BROKER_HOST,
                port=settings.MQTT_BROKER_PORT,
                keepalive=60,
            )
            self._client.loop_start()
        except Exception as e:
            logger.warning(
                f"[MQTT WARNING] Không thể khởi tạo socket tới Broker "
                f"({settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}): {e}. "
                f"Tiến trình nền sẽ tiếp tục thử kết nối lại khi Broker sẵn sàng."
            )
            # Vẫn chạy loop để client duy trì tiến trình retry nền
            try:
                self._client.loop_start()
            except Exception:
                pass

    def stop(self) -> None:
        """Dừng an toàn luồng mạng nền và đóng kết nối tới Mosquitto Broker."""
        logger.info("[MQTT] Đang dừng MQTT Client Service và giải phóng tài nguyên...")
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception as e:
            logger.warning(f"[MQTT] Lỗi khi ngắt kết nối MQTT Client: {e}")
        finally:
            self._is_connected = False


# Khởi tạo singleton instance sử dụng xuyên suốt hệ thống
mqtt_service = MQTTService()
