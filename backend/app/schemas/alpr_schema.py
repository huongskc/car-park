from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


IMAGE_ID_PATTERN = r"^[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg$"


class CaptureRequest(BaseModel):
    lane: Literal["IN", "OUT"]
    session_id: str | None = Field(default=None, max_length=64)


class CaptureResult(BaseModel):
    # Preserve OCR diagnostics, including suggested_plate and rejection_reasons.
    model_config = ConfigDict(extra="allow")
    request_id: str
    session_id: str | None = None
    lane: Literal["IN", "OUT"]
    status: Literal["success", "no_text_detected"]
    image_id: str = Field(pattern=IMAGE_ID_PATTERN)
    plate_number: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_confirmation: bool = True
    image_url: str = ""
