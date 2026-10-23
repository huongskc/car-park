# Smart Parking - Nhận diện biển số

Hệ thống Python dùng YOLO tìm biển số và OCR CCT/ONNX đọc ký tự.

## Code vận hành

- `camera.py`: mở webcam, chụp và nhận diện; lưu ảnh cùng kết quả JSON.
- `recognition.py`: nạp YOLO, chọn vùng biển số, cắt ảnh và điều phối OCR.
- `ocr.py`: OCR chuyên biển số; giữ EasyOCR làm phương án đối chứng/tùy chọn.
- `main.py`: dịch vụ FastAPI hiện có, dành cho tích hợp API khi cần.
- `models/`: trọng số YOLO và OCR. Model phụ hiện ở `images/DetectLisence_YOLO11.pt`.
- `images/`: ảnh và kết quả phát sinh khi vận hành.

## Cài thư viện

Yêu cầu Python 3.11 trở lên và webcam nếu chạy camera.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Chạy webcam

```powershell
.\.venv\Scripts\python.exe camera.py --ocr-engine plate
```

- `S`: chụp khung hình hiện tại, nhận diện và lưu ảnh/JSON trong `images`.
- `R`: chọn vùng tìm kiếm; lưu trong `camera_roi.json` cho lần chạy sau.
- `Q`: thoát, giải phóng camera.
- `--camera 1`: đổi camera; mặc định lấy `CAMERA_INDEX` hoặc 0.
- `--model`, `--conf`, `--imgsz`: chọn trọng số và cấu hình YOLO.
- `--ocr-engine auto|plate|easyocr`: chọn bộ đọc. `auto` ưu tiên model ONNX local.

Luồng chính: `camera.main()` -> `PlatePipeline.process()` ->
`PlateDetector.detect()` -> `recognize_frame()` -> `recognize_plate()`.

Hai tệp OCR cần giữ:
`models/plate_ocr/cct_s_v2_global.onnx` và
`models/plate_ocr/cct_s_v2_global_plate_config.yaml`.
Nguồn và hash trọng số được lưu trong `models/plate_ocr/source.json`.
Không tự tải model qua mạng trong lúc nhận diện.

Kết quả `candidate` vẫn cần đối chiếu ảnh gốc. `uncertain` hoặc
`no_plate_detected` không được xem là biển số đã xác nhận.

## API hiện có (tùy chọn)

Sao chép `.env.example` thành `.env` và đặt `ALPR_API_KEY` hợp lệ trước khi chạy:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Có `/health`, `POST /capture` và `GET /images/{image_id}`. Hai endpoint sau
cần header `X-API-Key`. API hiện có giữ nguyên luồng chụp riêng; công cụ
webcam dùng pipeline nhận diện đã cải thiện. Không mở hai luồng camera
cùng lúc. Backend đã có API proxy xác thực tại `/api/v1/alpr/capture` và
`/api/v1/alpr/images/{image_id}`; xem [hướng dẫn tích hợp](../docs/alpr_integration.md).

## Dữ liệu được giữ lại sau dọn dẹp

- `archive/`: ảnh mẫu, báo cáo kiểm thử và ảnh chẩn đoán cũ; không được code
  vận hành đọc. Đường dẫn trong CSV lịch sử có thể vẫn trỏ về vị trí cũ.
- `output/pdf/`: bản PDF đã xuất, giữ nguyên.

Các script benchmark, dashboard, tải mẫu, công cụ thử ảnh riêng và thư mục
unit test đã được bỏ theo yêu cầu. Chương trình không còn tự mở bảng thống
kê hoặc tạo CSV kiểm thử khi chạy camera; chỉ lưu kết quả vận hành JSON.
