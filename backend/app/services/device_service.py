import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.config import settings
from app.database import SessionLocal
from app.models.device import Device

logger = logging.getLogger("smart_parking.device_service")


class DeviceService:
    """
    Dịch vụ quản trị trạng thái kết nối và vòng đời thiết bị IoT.
    Cung cấp cơ chế quét tự động thiết bị quá hạn nhịp tim (Heartbeat Timeout Sweeper).
    """

    def expire_stale_devices(
        self,
        timeout_seconds: Optional[int] = None,
        db: Optional[Session] = None,
    ) -> list[str]:
        """
        Quét và chuyển trạng thái các thiết bị quá hạn nhịp tim sang Offline:
        - Mặc định sử dụng ngưỡng HEARTBEAT_TIMEOUT_SEC trong settings (30 giây).
        - Tìm các thiết bị đang có is_online == True nhưng last_heartbeat_at quá hạn hoặc bằng None.
        - Cập nhật is_online = False.
        - Trả về danh sách ID của các thiết bị vừa bị chuyển sang Offline.
        """
        if timeout_seconds is None:
            timeout_seconds = settings.HEARTBEAT_TIMEOUT_SEC

        close_session = False
        if db is None:
            db = SessionLocal()
            close_session = True

        stale_device_ids: list[str] = []
        try:
            cutoff_time = datetime.now() - timedelta(seconds=timeout_seconds)
            stale_filter = or_(
                Device.last_heartbeat_at <= cutoff_time,
                and_(Device.last_heartbeat_at.is_(None), Device.created_at <= cutoff_time),
                and_(Device.last_heartbeat_at.is_(None), Device.created_at.is_(None)),
            )
            candidate_ids = [row[0] for row in db.query(Device.id).filter(
                Device.is_online.is_(True), stale_filter,
            ).all()]
            for device_id in candidate_ids:
                # Recheck the timestamp in the UPDATE so a concurrent heartbeat wins.
                updated = db.query(Device).filter(
                    Device.id == device_id,
                    Device.is_online.is_(True),
                    stale_filter,
                ).update({Device.is_online: False}, synchronize_session=False)
                if updated:
                    stale_device_ids.append(device_id)

            if stale_device_ids:
                db.commit()
                logger.info(
                    f"[Device Service] Đã tự động chuyển {len(stale_device_ids)} thiết bị quá hạn sang Offline: {stale_device_ids}"
                )
        except Exception as e:
            db.rollback()
            logger.error(f"[Device Service] Lỗi khi quét thiết bị quá hạn nhịp tim: {e}", exc_info=True)
        finally:
            if close_session:
                db.close()

        return stale_device_ids


device_service = DeviceService()
