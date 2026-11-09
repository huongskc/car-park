# Backend ↔ AI service

## Cấu hình và chạy

1. Sao chép `ai-service/.env.example` thành `ai-service/.env`, đặt
   `ALPR_API_KEY` bằng một khóa ngẫu nhiên riêng.
2. Trong `.env` ở thư mục gốc, đặt `INTERNAL_ALPR_API_KEY` bằng đúng khóa trên,
   `ALPR_SERVICE_URL=http://127.0.0.1:8001`, `ALPR_TIMEOUT_SEC=60`.
3. Cài requirements của từng service bằng môi trường Python tương ứng.
4. Từ thư mục `ai-service`, chạy:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
   ```

5. Từ thư mục gốc, chạy backend:

   ```powershell
   python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```

Backend vẫn cần MySQL và cấu hình MQTT hiện có. Không mở cùng một webcam
đồng thời từ `camera.py`, API `/capture` và trình duyệt.

## Camera trực tiếp ở trang Vận hành cổng

- Mở frontend bằng `http://127.0.0.1:5500` hoặc HTTPS trong Chrome/Edge.
- Cắm hai webcam, vào **Vận hành cổng**, chọn camera cho **làn vào** và
  **làn ra**, rồi nhấn **Bật camera** và cho phép trình duyệt truy cập camera.
- Nếu chỉ có một webcam, có thể dùng chung để thử; giao diện sẽ thông báo
  hai làn đang dùng cùng một góc nhìn. Hai góc nhìn độc lập cần hai camera.
- **Đọc biển số** gửi khung hình hiện tại của đúng làn đến backend. Có thể
  bật **Tự đọc biển số**; hai làn được xử lý lần lượt, không chồng yêu cầu OCR.
  Khi một yêu cầu thất bại, chế độ tự đọc của làn đó dừng để người vận hành
  kiểm tra trước khi bật lại.
- Mỗi làn có biển số, độ tin cậy, thời điểm chụp và ảnh cắt để đối chiếu.
  **Dùng biển số cho lượt vào** chỉ điền ô nhập; nhân viên vẫn tự tạo lượt.
  Nhận diện không tự mở chắn, tạo phiên hoặc thu phí.
- Chế độ xem thử cho phép xem webcam thật nhưng không gọi nhận diện giả.
  Nhận diện thật cần đăng nhập OPERATOR/ADMIN, backend và AI service hoạt động.
- Rời trang hoặc đăng xuất sẽ tắt camera và hủy yêu cầu nhận diện đang chạy.
  Việc cập nhật bảng lệnh/phiên định kỳ không làm khởi động lại video.

### API ảnh từ camera trình duyệt

`POST /api/v1/alpr/recognize`, Bearer JWT của OPERATOR/ADMIN, multipart form:

- `lane`: `IN` hoặc `OUT`.
- `image`: ảnh JPEG/PNG, tối đa 4 MB; AI từ chối ảnh trên 16 triệu điểm ảnh.
- `session_id`: tùy chọn, tối đa 64 ký tự.

Backend gửi ảnh tới `POST /recognize` của AI bằng khóa nội bộ. AI áp dụng
`ROI_IN`/`ROI_OUT` trước khi tìm biển số, trả cùng hợp đồng kết quả và ảnh
riêng tư như `/capture`. Luồng này không mở webcam trên máy AI; nguồn video
là camera của máy đang mở trình duyệt. Không gửi API key hoặc JWT qua URL.

## Gọi API qua backend

`POST /api/v1/alpr/capture`, quyền OPERATOR hoặc ADMIN, header
`Authorization: Bearer <JWT>`:

```json
{"lane": "IN", "session_id": null}
```

`lane` là `IN` hoặc `OUT`. Backend tự sinh `request_id`, gửi `X-API-Key`
đến AI service và kiểm tra định danh yêu cầu trong phản hồi.
Kết quả có `plate_number`, `confidence`, `needs_confirmation`, các trường
chẩn đoán OCR và `image_url` tương đối, ví dụ
`/api/v1/alpr/images/capture_0123abcd.jpg`.

Không nhận diện được vẫn trả 200 với `plate_number=null`; không tạo biển số
giả. Giữ `needs_confirmation` để nhân viên đối chiếu ảnh trước khi xác nhận.
Sau khi xác nhận, truyền biển số và `image_url` vào `entry_plate` /
`entry_image_url` khi tạo phiên, hoặc `exit_plate` / `exit_image_url` khi checkout.
Capture không tự thay đổi phiên, thu phí hoặc mở barrier.

`GET /api/v1/alpr/images/{image_id}` cũng yêu cầu Bearer JWT và quyền vận hành.
Frontend cần fetch ảnh có Authorization rồi tạo blob URL để hiển thị;
không gắn token vào query string hoặc gọi AI service trực tiếp.

Lỗi: 409 camera bận, 503 thiếu cấu hình/mất kết nối/camera chưa sẵn sàng,
504 timeout, 502 lỗi xác thực hoặc phản hồi AI không hợp lệ, 404 ảnh không tồn tại.
Không tự retry capture để tránh chụp nhiều lần ngoài ý muốn.

## Kiểm tra hợp đồng tích hợp

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -p test_alpr_integration.py -v
.\ai-service\.venv\Scripts\python.exe -m unittest discover -s tests -p test_ai_camera_upload.py -v
```

Test dùng HTTP transport và OCR giả lập; không cần MySQL, broker, camera
hoặc model. `tests/test_gate_cameras.cjs` kiểm tra giao diện bằng Playwright
với hai nguồn video giả lập, gồm đổi/dùng chung camera, nhận diện, rời trang
và màn hình di động. Kiểm tra webcam và OCR thật thực hiện trên trang Vận hành cổng.
