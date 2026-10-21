import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import UserRole, DeviceStatus, CommandAction, CommandStatus
from app.models.device import Device
from app.models.command import Command
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.command_schema import CommandSend
from app.services.mqtt_service import mqtt_service

logger = logging.getLogger("smart_parking.command_service")


class CommandService:
    """
    Dịch vụ phát lệnh điều khiển phần cứng Barrier và xử lý phản hồi ACK.
    Quản lý luồng giao tiếp hai chiều giữa API Backend và vi điều khiển ESP32.
    """

    def send_command(
        self,
        device_id: str,
        payload: CommandSend,
        current_user: User,
        db: Session,
    ) -> Command:
        """
        Phát lệnh điều khiển phần cứng Barrier hoặc vi điều khiển ESP32:
        - Kiểm tra tính tồn tại và trạng thái kết nối thời gian thực của thiết bị.
        - Chặn an toàn (Safety Lock): Từ chối phát lệnh nếu thiết bị Offline hoặc Suspended.
        - Phân quyền theo rủi ro (Risk-based RBAC): Lệnh REBOOT/UPDATE_CONFIG chỉ dành cho ADMIN.
        - Lưu bản ghi Command ở trạng thái SENT và ghi vết vào AuditLog.
        - Publish bản tin JSON xuống MQTT Broker trên topic 'parking/{site_id}/{device_id}/commands'.
        """
        # 1. Kiểm tra thiết bị tồn tại
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy thiết bị có ID '{device_id}'.",
            )

        # 2. Phân quyền theo mức độ rủi ro (Risk-based RBAC: Kiểm tra trước trạng thái phần cứng)
        if payload.action in [CommandAction.REBOOT, CommandAction.UPDATE_CONFIG]:
            if current_user.role != UserRole.ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Chỉ Quản trị viên (ADMIN) mới có quyền thực thi lệnh kỹ thuật '{payload.action.value}'.",
                )

        # 3. Kiểm tra an toàn thiết bị (Hardware Safety Lock & Lifecycle)
        if device.status == DeviceStatus.DECOMMISSIONED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Thiết bị '{device_id}' đã ngừng sử dụng vĩnh viễn (DECOMMISSIONED), không thể tiếp nhận lệnh điều khiển.",
            )

        if device.status == DeviceStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Thiết bị '{device_id}' đang bị tạm ngưng hoạt động (SUSPENDED), vui lòng khôi phục trước khi phát lệnh.",
            )

        if device.status != DeviceStatus.ACTIVATED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Chỉ có thể phát lệnh điều khiển cho thiết bị đã kích hoạt (ACTIVATED). Trạng thái hiện tại của '{device_id}': {device.status.value}.",
            )

        # Kiểm tra tính tươi mới của nhịp tim (Heartbeat Freshness Check)
        if device.is_online:
            is_stale = False
            if device.last_heartbeat_at:
                is_stale = (datetime.now() - device.last_heartbeat_at).total_seconds() > settings.HEARTBEAT_TIMEOUT_SEC
            elif device.created_at:
                is_stale = (datetime.now() - device.created_at).total_seconds() > settings.HEARTBEAT_TIMEOUT_SEC

            if is_stale:
                device.is_online = False
                db.commit()
                db.refresh(device)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Thiết bị '{device_id}' đã quá hạn nhịp tim ({settings.HEARTBEAT_TIMEOUT_SEC}s) và được đánh dấu ngoại tuyến (Offline), không thể tiếp nhận lệnh.",
                )

        if not device.is_online:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Thiết bị '{device_id}' đang ngoại tuyến (Offline), không thể tiếp nhận lệnh điều khiển thời gian thực.",
            )

        # 4. Khởi tạo bản ghi Command mới
        command_id = f"cmd_{secrets.token_hex(8)}"
        new_command = Command(
            id=command_id,
            device_id=device.id,
            gate_id=payload.gate_id,
            action=payload.action,
            status=CommandStatus.SENT,
            sent_by_user_id=current_user.id,
        )
        db.add(new_command)

        # 5. Ghi vết kiểm toán bảo mật (Audit Log)
        audit = AuditLog(
            user_id=current_user.id,
            action=f"BARRIER_{payload.action.value}",
            resource_type="devices",
            resource_id=device.id,
            details=(
                f"Phát lệnh {payload.action.value} cho cổng {payload.gate_id.value} tại trạm {device.id} "
                f"(Mã lệnh: {command_id}). Lý do: {payload.reason or 'Không có'}"
            ),
        )
        db.add(audit)

        # 6. Ghi nhận giao dịch CSDL trước (Khắc phục Race Condition Publish-before-Commit)
        db.commit()
        db.refresh(new_command)

        # 7. Bắn bản tin JSON xuống MQTT Broker
        command_topic = f"{settings.MQTT_BASE_TOPIC}/{device.site_id}/{device.id}/commands"
        mqtt_payload = {
            "commandId": command_id,
            "gate": payload.gate_id.value,
            "action": payload.action.value,
            "reason": payload.reason,
            "sentAt": datetime.now().isoformat(),
        }
        publish_error = None
        try:
            msg_info = mqtt_service.publish(command_topic, mqtt_payload, qos=1)
            if getattr(msg_info, "rc", 0) != 0:
                publish_error = f"MQTT Publish RC={msg_info.rc}"
            else:
                logger.info(f"[Command Service] Đã phát lệnh '{command_id}' ({payload.action.value}) tới topic '{command_topic}'")
        except Exception as e:
            publish_error = str(e)

        if publish_error:
            logger.error(f"[Command Service] Lỗi khi publish MQTT cho lệnh '{command_id}': {publish_error}", exc_info=True)
            new_command.status = CommandStatus.FAILED
            new_command.ack_status = "PUBLISH_FAILED"
            new_command.ack_reason = publish_error
            db.commit()
            db.refresh(new_command)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": f"Không thể gửi lệnh điều khiển tới thiết bị qua MQTT: {publish_error}",
                    "command_id": command_id,
                    "status": "FAILED",
                },
            )

        return new_command

    def process_ack(self, payload: dict, db: Session, topic_device_id: Optional[str] = None) -> bool:
        """
        Xử lý bản tin ACK phản hồi từ vi điều khiển ESP32 gửi lên MQTT:
        - Đối soát device_id trên topic với device_id của lệnh trong CSDL.
        - Đảm bảo tính bất biến (Idempotency): Bỏ qua nếu lệnh đã kết thúc (ACKED/FAILED/EXPIRED).
        - Cập nhật trạng thái lệnh: ACKED (nếu thành công) hoặc FAILED.
        - Cập nhật mốc thời gian hoàn tất acked_at và lý do phản hồi ack_reason.
        """
        command_id = payload.get("commandId", payload.get("command_id"))
        if not command_id:
            logger.warning("[Command Service] Bản tin ACK thiếu commandId")
            return False

        command = db.query(Command).filter(Command.id == command_id).first()
        if not command:
            logger.warning(f"[Command Service] Nhận ACK cho lệnh không tồn tại trong CSDL: '{command_id}'")
            return False

        # 1. Đối soát device_id nếu được truyền vào từ topic
        if topic_device_id and command.device_id != topic_device_id:
            logger.warning(
                f"[Command Service] Thiết bị gửi ACK không khớp: Lệnh '{command_id}' thuộc về '{command.device_id}', "
                f"nhưng topic ACK đến từ '{topic_device_id}'"
            )
            return False

        # 2. Kiểm tra tính toàn vẹn của trường 'status' trong ACK
        raw_status = payload.get("status")
        if not raw_status:
            logger.warning("[Command Service] Bản tin ACK thiếu trường 'status'")
            return False

        ack_status_raw = str(raw_status).strip().upper()
        if ack_status_raw not in ["SUCCESS", "FAILED", "ERROR"]:
            logger.warning("[Command Service] Bản tin ACK có status không hợp lệ")
            return False

        # 3. Cập nhật có điều kiện trạng thái (Conditional Atomic Update)
        # Chỉ cập nhật nếu lệnh đang ở trạng thái SENT để chống Race Condition với Timeout Sweeper
        new_status = CommandStatus.ACKED if ack_status_raw == "SUCCESS" else CommandStatus.FAILED
        reason_val = payload.get("reason")

        rows_updated = (
            db.query(Command)
            .filter(
                Command.id == command_id,
                Command.status == CommandStatus.SENT,
            )
            .update(
                {
                    Command.status: new_status,
                    Command.ack_status: ack_status_raw,
                    Command.ack_reason: reason_val,
                    Command.acked_at: func.now(),
                },
                synchronize_session="fetch",
            )
        )
        db.commit()

        if rows_updated == 0:
            logger.info(
                f"[Command Service] Lệnh '{command_id}' đã ở trạng thái kết thúc ({command.status.value}), "
                f"bỏ qua ACK trùng lặp hoặc đến sau timeout."
            )
            return True

        if ack_status_raw == "SUCCESS":
            logger.info(f"[Command Service] Lệnh '{command_id}' đã được ESP32 thực thi thành công (ACKED)!")
        else:
            logger.warning(f"[Command Service] Lệnh '{command_id}' thất bại trên phần cứng: {reason_val}")

        return True

    def expire_stale_commands(self, timeout_seconds: int = 30, db: Optional[Session] = None) -> int:
        """
        Quét và chuyển các lệnh điều khiển ở trạng thái SENT quá thời gian chờ (timeout) sang EXPIRED.
        Sử dụng cập nhật có điều kiện (Atomic Conditional Update) để chống Race Condition với ACK.
        """
        close_db = False
        if db is None:
            from app.database import SessionLocal
            db = SessionLocal()
            close_db = True
        try:
            cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
            rows_updated = (
                db.query(Command)
                .filter(
                    Command.status == CommandStatus.SENT,
                    Command.sent_at <= cutoff,
                )
                .update(
                    {
                        Command.status: CommandStatus.EXPIRED,
                        Command.ack_status: "TIMEOUT",
                        Command.ack_reason: f"Lệnh vượt quá thời gian chờ phản hồi từ thiết bị ({timeout_seconds}s)",
                        Command.acked_at: func.now(),
                    },
                    synchronize_session="fetch",
                )
            )
            if rows_updated > 0:
                db.commit()
                logger.info(f"[Command Service] Đã chuyển {rows_updated} lệnh treo (SENT) sang trạng thái EXPIRED.")
            return rows_updated
        except Exception as e:
            db.rollback()
            logger.error(f"[Command Service] Lỗi khi quét lệnh timeout: {e}", exc_info=True)
            return 0
        finally:
            if close_db:
                db.close()


# Khởi tạo singleton instance
command_service = CommandService()
