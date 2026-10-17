import secrets
import logging
from datetime import datetime
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

        # 2. Kiểm tra an toàn thiết bị (Hardware Safety Lock)
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

        if not device.is_online:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Thiết bị '{device_id}' đang ngoại tuyến (Offline), không thể tiếp nhận lệnh điều khiển thời gian thực.",
            )

        # 3. Phân quyền theo mức độ rủi ro (Risk-based RBAC)
        if payload.action in [CommandAction.REBOOT, CommandAction.UPDATE_CONFIG]:
            if current_user.role != UserRole.ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Chỉ Quản trị viên (ADMIN) mới có quyền thực thi lệnh kỹ thuật '{payload.action.value}'.",
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

        # 6. Bắn bản tin JSON xuống MQTT Broker
        command_topic = f"{settings.MQTT_BASE_TOPIC}/{device.site_id}/{device.id}/commands"
        mqtt_payload = {
            "commandId": command_id,
            "gate": payload.gate_id.value,
            "action": payload.action.value,
            "reason": payload.reason,
            "sentAt": datetime.now().isoformat(),
        }
        mqtt_service.publish(command_topic, mqtt_payload, qos=1)
        logger.info(f"[Command Service] Đã phát lệnh '{command_id}' ({payload.action.value}) tới topic '{command_topic}'")

        db.commit()
        db.refresh(new_command)
        return new_command

    def process_ack(self, payload: dict, db: Session) -> bool:
        """
        Xử lý bản tin ACK phản hồi từ vi điều khiển ESP32 gửi lên MQTT:
        - Cập nhật trạng thái lệnh: ACKED (nếu thành công) hoặc FAILED.
        - Cập nhật mốc thời gian hoàn tất acked_at và lý do phản hồi ack_reason.
        """
        command_id = payload.get("commandId", payload.get("command_id"))
        if not command_id:
            logger.warning(f"[Command Service] Bản tin ACK thiếu commandId: {payload}")
            return False

        command = db.query(Command).filter(Command.id == command_id).first()
        if not command:
            logger.warning(f"[Command Service] Nhận ACK cho lệnh không tồn tại trong CSDL: '{command_id}'")
            return False

        ack_status_raw = str(payload.get("status", "SUCCESS")).upper()
        command.ack_status = ack_status_raw
        command.ack_reason = payload.get("reason")
        command.acked_at = func.now()

        if ack_status_raw == "SUCCESS":
            command.status = CommandStatus.ACKED
            logger.info(f"[Command Service] Lệnh '{command_id}' đã được ESP32 thực thi thành công (ACKED)!")
        else:
            command.status = CommandStatus.FAILED
            logger.warning(f"[Command Service] Lệnh '{command_id}' thất bại trên phần cứng: {command.ack_reason}")

        db.commit()
        return True


# Khởi tạo singleton instance
command_service = CommandService()
