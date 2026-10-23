import os
import re
import threading
from pathlib import Path
from uuid import uuid4
from ultralytics import YOLO
import numpy as np

import cv2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from ocr import recognize_plate

ENV_PATH = Path(__file__).resolve().parent / '.env'
load_dotenv(ENV_PATH, encoding='utf-8-sig')
KEY = os.getenv('ALPR_API_KEY', '')
if not KEY or KEY == 'replace-with-a-long-random-secret':
    raise RuntimeError(f'Cấu hình ALPR_API_KEY hợp lệ trong {ENV_PATH} trước khi chạy.')
CAMERA_INDEX = int(os.getenv('CAMERA_INDEX', '0'))
IMAGE_DIR = Path(__file__).parent / 'images'
IMAGE_DIR.mkdir(exist_ok=True)
CAMERA_LOCK = threading.Lock()
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


YOLO_MODEL  = os.getenv('YOLO_MODEL', 'models/license_plate.pt')
YOLO_CONF   = float(os.getenv('YOLO_CONF', '0.2'))
YOLO_IMG_SIZE = int(os.getenv('YOLO_IMG_SIZE', '640'))
YOLO_PAD    = float(os.getenv('YOLO_PAD_FACTOR', '0.05'))

if Path(YOLO_MODEL).is_file():
    plate_detector = YOLO(YOLO_MODEL)  # dùng CPU mặc định
else:
    plate_detector = None
    print("[WARN] YOLO model file not found – plate detection disabled.")


def detect_plate_bbox(frame: np.ndarray) -> tuple[int,int,int,int] | None:
    """
    Trả về bounding‑box (x1, y1, x2, y2) cho biển số.
    Trả None nếu không tìm được.
    """
    if plate_detector is None:
        return None

    # Ultralytics tự chuyển sang RGB nếu cần
    result = plate_detector(frame, conf=YOLO_CONF, imgsz=YOLO_IMG_SIZE, device="cpu", verbose=False)[0]

    if result.boxes is None or len(result.boxes) == 0:
        return None

    # Lấy kỳ thước có confidence cao nhất
    idx = int(np.argmax(result.boxes.conf.tolist()))
    x1, y1, x2, y2 = map(int, result.boxes.xyxy[idx].tolist())

    w, h = x2 - x1, y2 - y1
    pad_w, pad_h = int(w * YOLO_PAD), int(h * YOLO_PAD)

    # Bảo đảm biên trong ảnh
    x1 = max(0, x1 - pad_w)
    y1 = max(0, y1 - pad_h)
    x2 = min(frame.shape[1], x2 + pad_w)
    y2 = min(frame.shape[0], y2 + pad_h)

    return (x1, y1, x2, y2)

@app.post('/capture', dependencies=[Depends(authenticate)])
def capture(req: CaptureRequest):
    if not CAMERA_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Camera đang xử lý yêu cầu khác')
    try:
        frame = snap()

        # ① Thử YOLO đầu tiên
        bbox = detect_plate_bbox(frame)

        if bbox:
            crop = frame[bbox[1]:bbox[3], bbox[0]:bbox[2]]
        else:
            # ② Nếu YOLO ko tìm -> fallback tới ROI config (cũ)
            crop = roi_for(req.lane, frame)

        # ③ Tiếp tục logic đã có
        filename = f'{req.request_id}_{uuid4().hex[:8]}.jpg'
        if not cv2.imwrite(str(IMAGE_DIR / filename), crop):
            raise HTTPException(500, 'Không lưu được ảnh')
        result = recognize_plate(crop)

        return {
            'request_id': req.request_id,
            'session_id': req.session_id,
            'lane': req.lane,
            'status': 'success' if result['plate_number'] else 'no_text_detected',
            'image_id': filename,
            **result
        }
    finally:
        CAMERA_LOCK.release()


@app.get('/images/{image_id}', dependencies=[Depends(authenticate)])
def get_image(image_id: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]+_[0-9a-f]{8}\.jpg', image_id):
        raise HTTPException(400, 'image_id không hợp lệ')
    path = IMAGE_DIR / image_id
    if not path.is_file():
        raise HTTPException(404, 'Ảnh không tồn tại')
    return FileResponse(path, media_type='image/jpeg')
