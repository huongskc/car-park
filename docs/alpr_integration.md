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

Backend vẫn cần MySQL và cấu hình MQTT hiện có. Không chạy `camera.py` đồng
thời với AI API vì hai chương trình dùng chung camera.

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
python -m unittest discover -s tests -p test_alpr_integration.py -v
```

Test dùng HTTP transport giả lập; không cần MySQL, broker, camera hoặc model.
Kiểm tra thực tế qua Swagger `/docs` cần AI service và webcam hoạt động.
