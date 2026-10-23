"""Shared YOLO detection, crop selection and OCR orchestration.

Used by the standalone image and webcam tools; OCR lives in ocr.py.
"""
import math
import os
import re
import threading
from time import perf_counter
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent


def read_image(path):
    path = Path(path)
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError(f'Khong doc duoc anh: {path}')
    return image


class PlateDetector:
    def __init__(self, model=None, conf=None, imgsz=None, device='cpu', class_ids=None, fallback_model=None):
        from ultralytics import YOLO
        path = Path(model or os.getenv('YOLO_MODEL', 'models/license_plate.pt'))
        self.path = path if path.is_absolute() else BASE / path
        if not self.path.is_file():
            raise FileNotFoundError(f'Khong tim thay model: {self.path}')
        self.conf = float(conf if conf is not None else os.getenv('YOLO_CONF', '.2'))
        self.imgsz = int(imgsz if imgsz is not None else os.getenv('YOLO_IMG_SIZE', '960'))
        if not 0 < self.conf <= 1 or self.imgsz < 32:
            raise ValueError('conf phai trong (0,1], imgsz >= 32')
        self.device = device
        self.model = YOLO(str(self.path))
        if self.model.task != 'detect':
            raise ValueError('Can model YOLO object detection')
        names = self.model.names
        if class_ids is None:
            class_ids = [i for i, name in names.items()
                         if 'plate' in str(name).lower() or 'licence' in str(name).lower()]
            if not class_ids and len(names) == 1:
                class_ids = list(names)
        self.class_ids = [int(i) for i in (class_ids or [])]
        if not self.class_ids or any(i not in names for i in self.class_ids):
            raise ValueError(f'Chi dinh class_ids bien so. Cac lop model: {names}')
        self._lock = threading.Lock()
        secondary = fallback_model if fallback_model is not None else os.getenv(
            'YOLO_FALLBACK_MODEL', 'images/DetectLisence_YOLO11.pt')
        secondary = Path(secondary) if secondary else None
        if secondary is not None and not secondary.is_absolute():
            secondary = BASE / secondary
        self.fallback_path = secondary if secondary and secondary.is_file() and secondary.resolve() != self.path.resolve() else None
        self._fallback = None

    def detect(self, frame, include_fallback=False):
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError('Anh dau vao rong')
        height, width = frame.shape[:2]
        with self._lock:
            result = self.model.predict(frame, conf=self.conf, imgsz=self.imgsz,
                                        device=self.device, classes=self.class_ids,
                                        verbose=False)[0]
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                values = box.xyxy[0].tolist()
                if not all(math.isfinite(v) for v in values):
                    continue
                x1, y1, x2, y2 = values
                bounds = [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    continue
                cls = int(box.cls[0])
                detections.append({'bbox': bounds, 'confidence': float(box.conf[0]),
                                   'class_id': cls, 'class_name': self.model.names[cls]})
        for detection in detections:
            detection['model_path'] = str(self.path)
        if (not detections or include_fallback) and self.fallback_path is not None:
            with self._lock:
                if self._fallback is None:
                    self._fallback = PlateDetector(self.fallback_path, self.conf, self.imgsz,
                                                   self.device, fallback_model='')
            detections.extend(self._fallback.detect(frame))
        return rank_detections(detections, frame.shape)


def rank_detections(detections, shape):
    """Reject degenerate regions and prioritize readable area, not logo confidence."""
    height, width = shape[:2]
    kept = []
    for detection in detections:
        x1,y1,x2,y2 = detection['bbox']
        w,h = x2-x1,y2-y1
        if w < 16 or h < 8 or not .5 <= w/h <= 8:
            continue
        item = dict(detection)
        item['selection_score'] = item['confidence'] * min(1.0, math.sqrt(w*h/(.05*width*height)))
        kept.append(item)
    kept.sort(key=lambda d:d['selection_score'], reverse=True)
    unique=[]
    for detection in kept:
        a=detection['bbox']
        duplicate=False
        for other in unique:
            b=other['bbox']
            intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
            if intersection/max(union,1) > .85:
                duplicate=True
                break
        if not duplicate:
            unique.append(detection)
    return unique



def recognize_frame(frame, boxes, recognize, fallback=None, pad=0.08):
    """Return (crop, OCR result, source, frame bbox); never OCR a scene by default."""
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError('Anh dau vao rong')
    if not math.isfinite(pad) or not 0 <= pad <= .5:
        raise ValueError('pad phai trong [0, .5]')
    height, width = frame.shape[:2]
    best = None
    seen = set()
    attempts = []
    crop_evidence = {}

    def attempt(crop, source, bbox):
        nonlocal best
        result = recognize(crop)
        lines = result.get('lines', [])
        observed = result.get('suggested_plate')
        reasons = set(result.get('rejection_reasons', []))
        if (source == 'yolo' and observed and result.get('valid_format') and lines
                and min(line['confidence'] for line in lines) >= .65
                and reasons <= {'preprocessing_disagreement', 'insufficient_confident_agreement'}):
            evidence = crop_evidence.setdefault(observed, set())
            evidence.add(tuple(bbox))
            if len(evidence) >= 2:
                result = {**result, 'plate_number': observed, 'recognition_status': 'candidate',
                          'rejection_reasons': [], 'acceptance_basis': 'agreement_across_distinct_crops',
                          'agreeing_crops': len(evidence)}
        candidate = (crop, result, source, bbox)
        valid = bool(result.get('plate_number') and result.get('valid_format'))
        score = (valid, bool(result.get('valid_format')), float(result.get('confidence') or 0))
        attempts.append({'source': source, 'bbox': bbox,
                         'suggested_plate': result.get('suggested_plate'),
                         'confidence': result.get('confidence'), 'accepted': valid,
                         'valid_format': result.get('valid_format', False),
                         'min_line_confidence': min((line['confidence'] for line in lines), default=None),
                         'rejection_reasons': result.get('rejection_reasons', [])})
        extension = False
        if valid and best is not None:
            old = best[1][1]
            old_lines, new_lines = old.get('lines', []), result.get('lines', [])
            if len(old_lines) == len(new_lines) == 2:
                previous, current = old_lines[1]['text'], new_lines[1]['text']
                extension = (old_lines[0]['text'] == new_lines[0]['text']
                             and len(previous) == 4 and len(current) == 5
                             and (current.endswith(previous) or current.startswith(previous)))
            old_text = re.fullmatch(r'([0-9]{2}[A-Z]{1,2})([0-9]{4,5})', old.get('plate_number') or '')
            new_text = re.fullmatch(r'([0-9]{2}[A-Z]{1,2})([0-9]{4,5})', result.get('plate_number') or '')
            if old_text and new_text and old_text[1] == new_text[1]:
                previous, current = old_text[2], new_text[2]
                extension = extension or (len(previous) == 4 and len(current) == 5
                    and (current.startswith(previous) or current.endswith(previous)))
        if best is None or score > best[0] or extension:
            best = (score, candidate)
        # A four-digit row can be legitimate, but first check for clipped edges.
        lines = result.get('lines', [])
        short_single_row = bool(re.fullmatch(r'[0-9]{2}[A-Z]{1,2}[0-9]{4}', result.get('plate_number') or ''))
        return valid and not short_single_row and not (len(lines) == 2 and len(lines[1]['text']) == 4)

    for box in boxes[:3]:
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            continue
        x1, y1, x2, y2 = box
        bw, bh = x2-x1, y2-y1
        if bw <= 0 or bh <= 0:
            continue
        crop_evidence.clear()
        # Raw box, modest margin, then recovery if YOLO found only one row.
        for side, top, bottom in ((0, 0, 0), (pad, pad, pad),
                                  (.5, pad, pad), (pad, pad, 1.1), (pad, 1.1, pad)):
            bounds = [max(0, math.floor(x1-bw*side)), max(0, math.floor(y1-bh*top)),
                      min(width, math.ceil(x2+bw*side)), min(height, math.ceil(y2+bh*bottom))]
            left, upper, right, lower = bounds
            if right <= left or lower <= upper or tuple(bounds) in seen:
                continue
            seen.add(tuple(bounds))
            if attempt(frame[upper:lower, left:right], 'yolo', bounds):
                crop, result, source, bbox = best[1]
                return crop, {**result, 'crop_attempts': attempts}, source, bbox
    if fallback is not None:
        crop = fallback()
        if crop is not None and crop.size:
            attempt(crop, 'roi', None)
    if best is None:
        return None, {'plate_number': None, 'suggested_plate': None,
                      'valid_format': False, 'confidence': None, 'needs_confirmation': True,
                      'recognition_status': 'no_plate_detected', 'crop_attempts': []}, 'none', None
    crop, result, source, bbox = best[1]
    if not result.get('valid_format'):
        result = {**result, 'plate_number': None, 'suggested_plate': None,
                  'recognition_status': 'uncertain'}
    return crop, {**result, 'crop_attempts': attempts}, source, bbox


class PlatePipeline:
    def __init__(self, detector, pad=.08, ocr_engine=None):
        self.detector = detector
        self.pad = pad
        self.ocr_engine = ocr_engine

    def process(self, frame, roi=None):
        from ocr import recognize_plate
        started = perf_counter()
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("Anh dau vao rong")
        # An explicit search ROI avoids choosing a vehicle in another lane.
        ox = oy = 0
        search = frame
        bounds = None
        if roi is not None:
            if len(roi) != 4 or not all(math.isfinite(v) for v in roi):
                raise ValueError('ROI can x,y,w,h hop le')
            x, y, w, h = map(int, roi)
            if x < 0 or y < 0 or w < 2 or h < 2 or x+w > frame.shape[1] or y+h > frame.shape[0]:
                raise ValueError('ROI nam ngoai anh hoac qua nho')
            ox, oy = x, y
            bounds = [x, y, x+w, y+h]
            search = frame[y:y+h, x:x+w]
        detect_started = perf_counter()
        detections = self.detector.detect(search, include_fallback=True)
        detection_ms = (perf_counter() - detect_started) * 1000
        for detection in detections:
            x1, y1, x2, y2 = detection['bbox']
            detection['bbox'] = [x1+ox, y1+oy, x2+ox, y2+oy]
        ocr_started = perf_counter()
        crop, result, source, bbox = recognize_frame(
            frame, [d['bbox'] for d in detections],
            lambda crop: recognize_plate(crop, engine=self.ocr_engine),
            fallback=(lambda: search) if roi is not None else None, pad=self.pad)
        ocr_ms = (perf_counter() - ocr_started) * 1000
        if source == 'roi':
            bbox = bounds
        return crop, {**result, 'crop_source': source, 'plate_bbox': bbox,
                      'detections': detections, 'model_path': str(self.detector.path),
                      'timings_ms': {'detection_ms': round(detection_ms, 2),
                                     'ocr_ms': round(ocr_ms, 2),
                                     'processing_ms': round((perf_counter()-started)*1000, 2)}}
