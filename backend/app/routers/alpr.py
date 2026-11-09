from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Response, UploadFile

from app.core.dependencies import require_role
from app.models.enums import UserRole
from app.schemas.alpr_schema import CaptureRequest, CaptureResult, IMAGE_ID_PATTERN
from app.services.alpr_service import alpr_service


router = APIRouter(
    prefix="/alpr", tags=["License Plate Recognition"],
    dependencies=[Depends(require_role(UserRole.OPERATOR))],
)


@router.post("/capture", response_model=CaptureResult)
async def capture_plate(payload: CaptureRequest):
    return await alpr_service.capture(payload)


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
