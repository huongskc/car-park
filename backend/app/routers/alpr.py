from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import require_role
from app.database import get_db
from app.models.enums import UserRole, SessionStatus
from app.models.session import ParkingSession
from app.models.audit import AuditLog
from app.schemas.alpr_schema import CaptureRequest, CaptureResult, IMAGE_ID_PATTERN
from app.services.alpr_service import alpr_service
from app.services.parking_flow import prepare_exit, record_entry_ocr, normalize_plate


router = APIRouter(
    prefix="/alpr", tags=["License Plate Recognition"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.get("/preview")
async def preview_lane(lane: Literal["IN", "OUT"]):
    content = await alpr_service.preview(lane)
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/stream")
async def stream_lane(lane: Literal["IN", "OUT"]):
    return StreamingResponse(
        alpr_service.stream(lane),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.post("/capture", response_model=CaptureResult)
async def capture_plate(payload: CaptureRequest, db: Session = Depends(get_db)):
    if not payload.session_id:
        raise HTTPException(400, "Cần mã phiên để gắn ảnh chụp.")
    obj = db.query(ParkingSession).filter_by(session_code=payload.session_id).first()
    if not obj:
        raise HTTPException(404, "Không tìm thấy phiên gửi xe.")
    expected = SessionStatus.ENTRY_PENDING if payload.lane == "IN" else SessionStatus.ACTIVE
    if obj.status != expected:
        raise HTTPException(409, "Phiên không ở bước chụp ảnh của làn này.")
    result = await alpr_service.capture(payload)
    raw = result.plate_number or getattr(result, "suggested_plate", None)
    confident = result.status == "success" and not result.needs_confirmation and bool(result.plate_number)
    if payload.lane == "IN":
        record_entry_ocr(db, obj.id, raw, result.image_url, auto_confirm=confident)
    elif confident:
        prepare_exit(db, obj.id, result.plate_number, result.image_url,
                     needs_confirmation=False, ocr_plate=result.plate_number)
    else:
        obj.exit_ocr_plate = raw.strip().upper() if normalize_plate(raw) else None
        obj.exit_image_url = result.image_url
        db.add(AuditLog(action="EXIT_OCR_CAPTURED", resource_type="parking_sessions",
                        resource_id=str(obj.id), details="Ảnh OCR từ camera làn ra"))
        db.commit()
    return result


@router.post("/recognize", response_model=CaptureResult)
async def recognize_camera_frame(
    lane: Annotated[Literal["IN", "OUT"], Form()],
    image: Annotated[UploadFile, File()],
    session_id: Annotated[str | None, Form(max_length=64)] = None,
):
    """Read a browser camera frame without opening the edge service's webcam."""
    try:
        if image.content_type not in ("image/jpeg", "image/png"):
            raise HTTPException(415, "Chỉ hỗ trợ ảnh JPEG hoặc PNG.")
        content = await image.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise HTTPException(413, "Ảnh không được lớn hơn 4 MB.")
        if not content:
            raise HTTPException(400, "Ảnh gửi lên đang trống.")
        return await alpr_service.recognize(
            CaptureRequest(lane=lane, session_id=session_id), content, image.content_type
        )
    finally:
        await image.close()


@router.get("/images/{image_id}", response_class=Response)
async def get_plate_image(image_id: Annotated[str, Path(pattern=IMAGE_ID_PATTERN)]):
    content = await alpr_service.image(image_id)
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
