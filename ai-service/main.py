import os
import re
import sys
import threading
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from ultralytics import YOLO
import numpy as np

import cv2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ocr import recognize_plate
from recognition import PlateDetector, recognize_frame

ENV_PATH = BASE_DIR / '.env'
load_dotenv(ENV_PATH, encoding='utf-8-sig')
KEY = os.getenv('ALPR_API_KEY', '')
if not KEY or KEY == 'replace-with-a-long-random-secret':
    raise RuntimeError(f'Cấu hình ALPR_API_KEY hợp lệ trong {ENV_PATH} trước khi chạy.')
CAMERA_INDEX = int(os.getenv('CAMERA_INDEX', '0'))
IMAGE_DIR = BASE_DIR / 'images'
IMAGE_DIR.mkdir(exist_ok=True)
MAX_STORED_IMAGES = int(os.getenv('MAX_STORED_IMAGES', '500'))
CAMERA_LOCK = threading.Lock()
_ACTIVE_RECOGNIZE_WORKERS = 0
app = FastAPI(title='Smart Parking ALPR Edge Service', version='0.1.0')

class CaptureRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    lane: Literal['IN', 'OUT']
    session_id: str | None = Field(default=None, max_length=64)


def authenticate(x_api_key: str | None = Header(default=None)):
    import secrets
    if not x_api_key or not secrets.compare_digest(x_api_key, KEY):
        raise HTTPException(status_code=401, detail='API key không hợp lệ')


def roi_for(lane: str, frame):
    raw = os.getenv('ROI_' + lane, '0,0,1,1')
    try:
        x1, y1, x2, y2 = (float(v.strip()) for v in raw.split(','))
    except ValueError as exc:
        raise HTTPException(500, f'ROI_{lane} không hợp lệ') from exc
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise HTTPException(500, f'ROI_{lane} phải thỏa 0<=x1<x2<=1, 0<=y1<y2<=1')
    h, w = frame.shape[:2]
    return frame[int(y1*h):int(y2*h), int(x1*w):int(x2*w)]


def snap():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        cap.release()
        raise HTTPException(503, 'Không mở được webcam')
    try:
        frame = None
        for _ in range(8):
            ok, current = cap.read()
            if ok:
                frame = current
        if frame is None:
            raise HTTPException(503, 'Không đọc được ảnh từ webcam')
        return frame
    finally:
        cap.release()


@app.get('/health')
def health():
    return {'status': 'ok'}


YOLO_MODEL = os.getenv('YOLO_MODEL', 'models/license_plate.pt').split('#')[0].strip()
YOLO_CONF = float(os.getenv('YOLO_CONF', '0.15').split('#')[0].strip())
YOLO_IMG_SIZE = int(os.getenv('YOLO_IMG_SIZE', '640').split('#')[0].strip())
YOLO_PAD = float(os.getenv('YOLO_PAD_FACTOR', '0.08').split('#')[0].strip())

_model_path = Path(YOLO_MODEL) if Path(YOLO_MODEL).is_absolute() else (BASE_DIR / YOLO_MODEL)
if _model_path.is_file():
    try:
        plate_detector = PlateDetector(model=_model_path, conf=YOLO_CONF, imgsz=YOLO_IMG_SIZE)
    except Exception:
        plate_detector = YOLO(str(_model_path))
else:
    plate_detector = None
    print("[WARN] YOLO model file not found – plate detection disabled.")


def detect_plate_bbox(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Trả về bounding‑box (x1, y1, x2, y2) tốt nhất cho biển số (kèm lưu danh sách
    detections từ cả model chính & fallback model nếu dùng PlateDetector).
    Trả None nếu không tìm được.
    """
    if plate_detector is None:
        return None

    if hasattr(plate_detector, 'detect'):
        detections = plate_detector.detect(frame, include_fallback=True)
        plate_detector._last_detections = detections
        if not detections:
            return None
        x1, y1, x2, y2 = map(int, detections[0]['bbox'])
        return (x1, y1, x2, y2)

    result = plate_detector(frame, conf=YOLO_CONF, imgsz=YOLO_IMG_SIZE, device="cpu", verbose=False)[0]
    if result.boxes is None or len(result.boxes) == 0:
        return None

    idx = int(np.argmax(result.boxes.conf.tolist()))
    x1, y1, x2, y2 = map(int, result.boxes.xyxy[idx].tolist())

    w, h = x2 - x1, y2 - y1
    pad_w, pad_h = int(w * YOLO_PAD), int(h * YOLO_PAD)

    x1 = max(0, x1 - pad_w)
    y1 = max(0, y1 - pad_h)
    x2 = min(frame.shape[1], x2 + pad_w)
    y2 = min(frame.shape[0], y2 + pad_h)

    return (x1, y1, x2, y2)


def _cleanup_old_images():
    if IMAGE_DIR != BASE_DIR / 'images' or MAX_STORED_IMAGES <= 0:
        return
    try:
        jpgs = [
            p for p in IMAGE_DIR.glob('*.jpg')
            if re.fullmatch(r'[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg', p.name)
        ]
        if len(jpgs) > MAX_STORED_IMAGES:
            jpgs.sort(key=lambda p: p.stat().st_mtime)
            for old_file in jpgs[: len(jpgs) - MAX_STORED_IMAGES]:
                old_file.unlink(missing_ok=True)
    except OSError:
        pass


@app.post('/capture', dependencies=[Depends(authenticate)])
def capture(req: CaptureRequest):
    if not CAMERA_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Camera đang xử lý yêu cầu khác')
    try:
        return process_frame(req, snap())
    finally:
        CAMERA_LOCK.release()


def process_frame(req: CaptureRequest, frame: np.ndarray):
    # Restrict detection to the selected lane before searching for a plate.
    lane_frame = roi_for(req.lane, frame)
    if not lane_frame.size:
        raise HTTPException(400, 'Vùng nhận diện không có dữ liệu ảnh')
    bbox = detect_plate_bbox(lane_frame)
    if plate_detector is not None and hasattr(plate_detector, 'detect'):
        raw_boxes = [d['bbox'] for d in getattr(plate_detector, '_last_detections', [])]
        if not raw_boxes and bbox:
            raw_boxes = [list(bbox)]
        if len(raw_boxes) >= 2:
            b1, b2 = raw_boxes[0], raw_boxes[1]
            if abs(b1[1] - b2[1]) < 50:
                merged = [min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3])]
                raw_boxes.insert(0, merged)
        best_crop, result, _, _ = recognize_frame(
            lane_frame,
            raw_boxes,
            recognize_plate,
            fallback=None,
            pad=YOLO_PAD,
        )
        crop = best_crop if (best_crop is not None and best_crop.size) else (lane_frame[bbox[1]:bbox[3], bbox[0]:bbox[2]] if bbox else lane_frame)
    else:
        crop = lane_frame[bbox[1]:bbox[3], bbox[0]:bbox[2]] if bbox else lane_frame
        result = recognize_plate(crop)
    filename = f'{req.request_id}_{uuid4().hex[:8]}.jpg'
    if not cv2.imwrite(str(IMAGE_DIR / filename), crop):
        raise HTTPException(500, 'Không lưu được ảnh')
    _cleanup_old_images()
    return {
        **result,
        'request_id': req.request_id,
        'session_id': req.session_id,
        'lane': req.lane,
        'status': 'success' if result.get('plate_number') else 'no_text_detected',
        'image_id': filename,
    }


def decode_frame(content: bytes) -> np.ndarray:
    if not content:
        raise HTTPException(400, 'Ảnh gửi lên đang trống')
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in ('JPEG', 'PNG'):
                raise HTTPException(415, 'Chỉ hỗ trợ ảnh JPEG hoặc PNG')
            if image.width * image.height > 16_000_000:
                raise HTTPException(413, 'Ảnh không được vượt quá 16 triệu điểm ảnh')
            image.verify()
        frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, cv2.error):
        raise HTTPException(400, 'Không đọc được dữ liệu ảnh') from None
    if frame is None:
        raise HTTPException(400, 'Không đọc được dữ liệu ảnh')
    return frame


@app.post('/recognize', dependencies=[Depends(authenticate)])
def recognize_camera_frame(
    request_id: str = Form(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$'),
    lane: Literal['IN', 'OUT'] = Form(),
    image: UploadFile = File(),
    session_id: str | None = Form(default=None, max_length=64),
):
    """Accept a lane's live browser frame; never acquire a physical camera here."""
    try:
        if image.content_type not in ('image/jpeg', 'image/png'):
            raise HTTPException(415, 'Chỉ hỗ trợ ảnh JPEG hoặc PNG')
        content = image.file.read(4 * 1024 * 1024 + 1)
    finally:
        image.file.close()
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(413, 'Ảnh không được lớn hơn 4 MB')
    global _ACTIVE_RECOGNIZE_WORKERS
    acquired = CAMERA_LOCK.acquire(timeout=30.0)
    if not acquired:
        raise HTTPException(409, 'Bộ nhận diện đang xử lý yêu cầu khác')
    _ACTIVE_RECOGNIZE_WORKERS += 1
    try:
        return process_frame(
            CaptureRequest(request_id=request_id, lane=lane, session_id=session_id),
            decode_frame(content),
        )
    finally:
        _ACTIVE_RECOGNIZE_WORKERS -= 1
        CAMERA_LOCK.release()


@app.get('/images/{image_id}', dependencies=[Depends(authenticate)])
def get_image(image_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg', image_id):
        raise HTTPException(400, 'image_id không hợp lệ')
    path = IMAGE_DIR / image_id
    if not path.is_file():
        raise HTTPException(404, 'Ảnh không tồn tại')
    return FileResponse(path, media_type='image/jpeg')
