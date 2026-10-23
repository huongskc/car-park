"""Smart Parking: S = capture + OCR, R = choose plate ROI, Q = quit.

Run: .\\.venv\\Scripts\\python.exe camera.py
Requires ocr.py in the same directory with recognize_plate(image).
"""
import json
import os
import argparse
from time import perf_counter
from dotenv import load_dotenv
from recognition import PlateDetector, PlatePipeline
from datetime import datetime
from pathlib import Path
import cv2
BASE = Path(__file__).resolve().parent
IMAGES = BASE / 'images'
CONFIG = BASE / 'camera_roi.json'

def load_roi():
    if CONFIG.exists():
        try:
            values = json.loads(CONFIG.read_text(encoding='utf-8'))
            if isinstance(values, list) and len(values) == 4:
                return tuple((int(v) for v in values))
        except (ValueError, OSError, TypeError):
            pass
    return None

def get_plate_region(frame, roi):
    if roi is None:
        return None
    x, y, w, h = roi
    height, width = frame.shape[:2]
    if x < 0 or y < 0 or w <= 0 or (h <= 0) or (x + w > width) or (y + h > height):
        return None
    return frame[y:y + h, x:x + w].copy()

def main():
    load_dotenv(BASE / '.env')
    parser = argparse.ArgumentParser(description='Webcam YOLO + OCR; R: ROI, S: chup, Q: thoat')
    parser.add_argument('--model')
    parser.add_argument('--conf', type=float)
    parser.add_argument('--imgsz', type=int)
    parser.add_argument('--camera', type=int, default=int(os.getenv('CAMERA_INDEX', '0')))
    parser.add_argument('--ocr-engine', choices=['auto', 'plate', 'easyocr'], default=None)
    args = parser.parse_args()
    pipeline = PlatePipeline(PlateDetector(args.model, args.conf, args.imgsz), ocr_engine=args.ocr_engine)
    IMAGES.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError('Khong mo duoc camera. Kiem tra quyen va Camera Index.')
    roi = load_roi()
    print('R: chon khung bien so (mot lan, luu lai) | S: chup va OCR | Q: thoat')
    print('YOLO tu tim bien so; R gioi han vung tim kiem neu can. ROI cu duoc nap lai.')
    try:
        while True:
            capture_started = perf_counter()
            ok, frame = cap.read()
            capture_ms = round((perf_counter() - capture_started) * 1000, 2)
            if not ok:
                print('Khong doc duoc khung hinh camera')
                break
            preview = frame.copy()
            if roi is not None:
                plate = get_plate_region(frame, roi)
                if plate is not None:
                    x, y, w, h = roi
                    cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 255, 0), 2)
                else:
                    roi = None
                    print('ROI khong phu hop kich thuoc camera, nhan R de chon lai')
            cv2.putText(preview, 'R: ROI | S: Capture+OCR | Q: Quit', (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
            cv2.imshow('Smart Parking - Camera', preview)
            key = cv2.waitKey(1) & 255
            if key in (ord('q'), ord('Q')):
                break
            if key in (ord('r'), ord('R')):
                selection = cv2.selectROI('Select PLATE area, press ENTER', frame, fromCenter=False, showCrosshair=True)
                cv2.destroyWindow('Select PLATE area, press ENTER')
                if int(selection[2]) > 0 and int(selection[3]) > 0:
                    roi = tuple(map(int, selection))
                    CONFIG.write_text(json.dumps(list(roi)), encoding='utf-8')
                    print(f'Da luu ROI: {roi}')
                else:
                    print('Chua chon ROI; giu ROI cu')
            if key in (ord('s'), ord('S')):
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                original_path = IMAGES / f'capture_{timestamp}.jpg'
                cropped_path = IMAGES / f'plate_{timestamp}.jpg'
                result_path = IMAGES / f'result_{timestamp}.json'
                if not cv2.imwrite(str(original_path), frame):
                    print('Loi luu anh goc; bo qua anh nay')
                    continue
                result = {}
                cropped = None
                try:
                    cropped, result = pipeline.process(frame, roi)
                    if cropped is not None and (not cv2.imwrite(str(cropped_path), cropped)):
                        raise RuntimeError('Khong luu duoc anh bien so')
                    payload = {'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'capture_ms': capture_ms, 'original_image': original_path.name, 'plate_image': cropped_path.name if cropped is not None else None, **result}
                    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, 'item') else str(x)), encoding='utf-8')
                    print('\n----- KET QUA OCR -----')
                    print('Bien so:', result.get('plate_number') or 'Khong nhan dien duoc')
                    print('Hop dinh dang:', result.get('valid_format'))
                    print('Confidence:', result.get('confidence'))
                    print('Can xac nhan:', result.get('needs_confirmation'))
                    print('Anh goc:', original_path.name)
                    print('Anh bien so:', cropped_path.name if cropped is not None else 'Khong tim thay')
                    print('Bao cao:', result_path.name)
                    print('-----------------------\n')
                except Exception as exc:
                    print('OCR that bai:', type(exc).__name__, str(exc))
                    print('Anh da duoc luu de thu lai:', cropped_path)
    finally:
        cap.release()
        cv2.destroyAllWindows()
if __name__ == '__main__':
    main()
