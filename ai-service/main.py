import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from ultralytics import YOLO
import numpy as np

import cv2
import torch
from dotenv import load_dotenv

# Limit PyTorch CPU threads so EasyOCR doesn't starve the webcam thread and web server
try:
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 2 or 2)))
except Exception:
    pass
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
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
OCR_LOCK = threading.Lock()

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


class LiveCamera:
    """Keep one webcam open so the dashboard can show a live frame."""

    def __init__(self):
        self._frame = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = None
        self._frame_seq = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='webcam', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def _open(self):
        # On Windows, use DirectShow (CAP_DSHOW) for instant webcam opening and low latency
        if sys.platform == "win32":
            cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(CAMERA_INDEX)
        else:
            cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(CAMERA_INDEX)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap

    def _loop(self):
        cap = None
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                cap = self._open()
                if not cap.isOpened():
                    cap = None
                    time.sleep(0.5)
                    continue
            ok, frame = cap.read()
            if not ok or frame is None:
                if cap is not None:
                    cap.release()
                cap = None
                time.sleep(0.2)
                continue
            with self._cond:
                self._frame = frame
                self._frame_seq += 1
                self._cond.notify_all()
        if cap is not None:
            cap.release()

    def frame(self):
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def fresh_frame(self, skip_count: int = 2, timeout: float = 0.5):
        """Wait for `skip_count` brand-new frames from the camera to flush hardware/driver buffers."""
        with self._cond:
            start_seq = self._frame_seq
            target_seq = self._frame_seq + skip_count
            start = time.time()
            while self._frame_seq < target_seq and not self._stop.is_set():
                remaining = timeout - (time.time() - start)
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            if self._frame is None or self._frame_seq == start_seq:
                return None
            return self._frame.copy()


live_camera = LiveCamera()


@asynccontextmanager
async def lifespan(_app):
    live_camera.start()
    yield
    live_camera.stop()


app = FastAPI(title='Smart Parking ALPR Edge Service', version='0.1.0', lifespan=lifespan)


def snap(wait_fresh: bool = False):
    if wait_fresh:
        frame = live_camera.fresh_frame(skip_count=2, timeout=0.6)
        if frame is None:
            raise HTTPException(503, 'Webcam chưa có khung hình mới; vui lòng thử lại')
    else:
        frame = live_camera.frame()
    if frame is None:
        raise HTTPException(503, 'Không đọc được ảnh từ webcam')
    return frame


def jpeg_bytes(frame: np.ndarray) -> bytes:
    height, width = frame.shape[:2]
    if width > 640:
        frame = cv2.resize(frame, (640, max(1, int(height * 640 / width))))
    ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
    if not ok:
        raise HTTPException(500, 'Không tạo được ảnh xem trước')
    return encoded.tobytes()


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


def detect_displayed_plate(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """Find a two-line plate rectangle shown on a phone during the software demo."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(cv2.Canny(gray, 60, 150),
                       cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        if (area >= 0.05 * width * height and x > 2 and y > 2
                and x + w < width - 2 and y + h < height - 2
                and 0.8 <= w / h <= 2.5 and area / (w * h) > 0.65
                and len(cv2.approxPolyDP(contour, 0.03 * cv2.arcLength(contour, True), True)) == 4):
            candidates.append((area, (x, y, x + w, y + h)))
    return max(candidates, default=(0, None))[1]


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


@app.get('/preview', dependencies=[Depends(authenticate)])
def preview(lane: Literal['IN', 'OUT'] = Query()):
    crop = roi_for(lane, snap())
    if not crop.size:
        raise HTTPException(400, 'Vùng nhận diện không có dữ liệu ảnh')
    return Response(jpeg_bytes(crop), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.post('/capture', dependencies=[Depends(authenticate)])
def capture(req: CaptureRequest):
    if not CAMERA_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Camera đang xử lý yêu cầu khác')
    try:
        # Snap a fresh frame that arrived AFTER the capture request was triggered!
        frame = snap(wait_fresh=True)
    finally:
        CAMERA_LOCK.release()
    return process_frame(req, frame)


@app.post('/snapshot', dependencies=[Depends(authenticate)])
def snapshot(req: CaptureRequest):
    """Return the exact saved frame before OCR starts."""
    if not CAMERA_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Camera đang xử lý yêu cầu khác')
    try:
        lane_frame = roi_for(req.lane, snap(wait_fresh=True))
        if not lane_frame.size:
            raise HTTPException(400, 'Vùng nhận diện không có dữ liệu ảnh')
        filename = save_lane_image(req, lane_frame)
    finally:
        CAMERA_LOCK.release()
    return Response((IMAGE_DIR / filename).read_bytes(), media_type='image/jpeg',
                    headers={'X-Image-Id': filename, 'Cache-Control': 'no-store'})


def save_lane_image(req: CaptureRequest, lane_frame: np.ndarray) -> str:
    filename = f'{req.request_id}_{uuid4().hex[:8]}.jpg'
    if not cv2.imwrite(str(IMAGE_DIR / filename), lane_frame):
        raise HTTPException(500, 'Không lưu được ảnh')
    _cleanup_old_images()
    return filename


def process_frame(req: CaptureRequest, frame: np.ndarray, already_cropped: bool = False):
    # Snapshot JPEG already contains one lane ROI; never crop it a second time.
    lane_frame = frame if already_cropped else roi_for(req.lane, frame)
    if not lane_frame.size:
        raise HTTPException(400, 'Vùng nhận diện không có dữ liệu ảnh')

    # Always save the full lane frame so the evidence image is clear and matches preview
    filename = save_lane_image(req, lane_frame)

    # Keep gate OCR bounded; unclear plates remain for operator review.
    best_result = {}
    bbox = detect_displayed_plate(lane_frame)
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        best_result = recognize_plate(lane_frame[y1:y2, x1:x2], engine='plate',
                                      allow_heavy_fallback=False)
    if not best_result.get('plate_number') or best_result.get('needs_confirmation', True):
        detected = detect_plate_bbox(lane_frame)
        if detected is not None and detected != bbox:
            x1, y1, x2, y2 = detected
            result = recognize_plate(lane_frame[y1:y2, x1:x2], engine='plate',
                                     allow_heavy_fallback=False)
            if (bool(result.get('plate_number')), float(result.get('confidence') or 0)) > (
                    bool(best_result.get('plate_number')), float(best_result.get('confidence') or 0)):
                best_result = result

    plate = best_result.get('plate_number') or best_result.get('suggested_plate')
    conf = best_result.get('confidence')
    needs_confirm = best_result.get('needs_confirmation', not bool(plate))
    if conf and conf >= 0.85 and bool(best_result.get('plate_number')):
        needs_confirm = False

    return {
        **best_result,
        'plate_number': plate,
        'suggested_plate': plate,
        'needs_confirmation': needs_confirm,
        'request_id': req.request_id,
        'session_id': req.session_id,
        'lane': req.lane,
        'status': 'success' if plate else 'no_text_detected',
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
    image_is_lane_crop: bool = Form(default=False),
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
    acquired = OCR_LOCK.acquire(timeout=3.0)
    if not acquired:
        raise HTTPException(409, 'Bộ nhận diện đang xử lý yêu cầu khác')
    try:
        return process_frame(
            CaptureRequest(request_id=request_id, lane=lane, session_id=session_id),
            decode_frame(content),
            already_cropped=image_is_lane_crop,
        )
    finally:
        OCR_LOCK.release()


@app.get('/images/{image_id}', dependencies=[Depends(authenticate)])
def get_image(image_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg', image_id):
        raise HTTPException(400, 'image_id không hợp lệ')
    path = IMAGE_DIR / image_id
    if not path.is_file():
        raise HTTPException(404, 'Ảnh không tồn tại')
    return FileResponse(path, media_type='image/jpeg')
