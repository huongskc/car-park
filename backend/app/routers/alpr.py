from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

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


@router.get("/images/{image_id}", response_class=Response)
async def get_plate_image(image_id: Annotated[str, Path(pattern=IMAGE_ID_PATTERN)]):
    content = await alpr_service.image(image_id)
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
