"""Clearly labelled ALPR mock for the software demo. Run with ai-service/.venv."""
import io
import os
import secrets
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

KEY = os.environ.get("ALPR_API_KEY", "")
if not KEY:
    raise RuntimeError("Set ALPR_API_KEY to the backend INTERNAL_ALPR_API_KEY.")

app = FastAPI(title="DEMO ALPR mock")
guard = Lock()
plates = {"IN": "30A12345", "OUT": "30A12345"}
review = {"IN": False, "OUT": False}
images: dict[str, bytes] = {}


class Scenario(BaseModel):
    entry_plate: str | None = Field(default="30A12345", max_length=20)
    exit_plate: str | None = Field(default="30A12345", max_length=20)
    entry_needs_confirmation: bool = False
    exit_needs_confirmation: bool = False


class Capture(BaseModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    lane: str
    session_id: str | None = None


def authenticate(x_api_key: str | None):
    if not x_api_key or not secrets.compare_digest(x_api_key, KEY):
        raise HTTPException(401, "Invalid demo key")


@app.get("/health")
def health():
    return {"status": "demo", "engine": "mock"}


@app.post("/scenario")
def set_scenario(scenario: Scenario, x_api_key: str | None = Header(None)):
    authenticate(x_api_key)
    with guard:
        plates.update(IN=scenario.entry_plate, OUT=scenario.exit_plate)
        review.update(IN=scenario.entry_needs_confirmation,
                      OUT=scenario.exit_needs_confirmation)
    return {"status": "demo scenario loaded"}


def create_demo_capture(req: Capture):
    if req.lane not in plates:
        raise HTTPException(422, "Invalid lane")
    with guard:
        plate = plates[req.lane]
        needs_confirmation = review[req.lane] or plate is None
    filename = f"{req.request_id}_{uuid4().hex[:8]}.jpg"
    picture = Image.new("RGB", (500, 180), "white")
    draw = ImageDraw.Draw(picture)
    draw.text((15, 15), "DEMO - OCR MOCK, NOT CAMERA", fill="red")
    draw.text((30, 90), plate or "NO PLATE", fill="black")
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=85)
    with guard:
        images[filename] = buffer.getvalue()
    return {"request_id": req.request_id, "session_id": req.session_id,
            "lane": req.lane, "status": "success" if plate else "no_text_detected",
            "image_id": filename, "plate_number": plate, "confidence": None,
            "needs_confirmation": needs_confirmation, "recognition_status": "mock", "demo": True}


@app.post("/capture")
def capture(req: Capture, x_api_key: str | None = Header(None)):
    authenticate(x_api_key)
    return create_demo_capture(req)


@app.post("/snapshot")
def snapshot(req: Capture, x_api_key: str | None = Header(None)):
    authenticate(x_api_key)
    result = create_demo_capture(req)
    return Response(images[result["image_id"]], media_type="image/jpeg",
                    headers={"X-Image-Id": result["image_id"], "Cache-Control": "no-store"})


@app.post("/recognize")
def recognize(request_id: str = Form(), lane: str = Form(), image: UploadFile = File(),
              session_id: str | None = Form(None), x_api_key: str | None = Header(None)):
    authenticate(x_api_key)
    image.file.close()
    return create_demo_capture(Capture(request_id=request_id, lane=lane, session_id=session_id))


@app.get("/images/{image_id}")
def image(image_id: str, x_api_key: str | None = Header(None)):
    authenticate(x_api_key)
    with guard:
        data = images.get(image_id)
    if data is None:
        raise HTTPException(404, "Demo image not found")
    return Response(data, media_type="image/jpeg")
