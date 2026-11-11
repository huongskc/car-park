# Tích hợp camera và ALPR

## Cấu hình

Một webcam laptop đặt giữa hai làn do **AI service** mở. Đặt `ROI_IN` và
`ROI_OUT` trong `ai-service/.env` theo khung hình thực tế; hai vùng này
phải được kiểm chứng khi lắp mô hình. Đặt `ALPR_API_KEY` trong AI service
và cùng giá trị ở `INTERNAL_ALPR_API_KEY` của backend. Backend dùng
`ALPR_SERVICE_URL=http://127.0.0.1:8001`. Không đưa API key xuống web.

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Backend vẫn cần MySQL và MQTT theo cấu hình dự án.

## Luồng

RFID vào tạo phiên và giữ ô trước, rồi backend gọi AI `POST /snapshot`
với `lane=IN` và `session_id` là mã phiên. RFID ra làm tương tự với
`lane=OUT`. AI chọn ROI của làn nhưng chỉ mở **một webcam**. Backend lưu
URL JPEG và phát cập nhật dashboard ngay sau snapshot; sau đó gửi chính
ảnh JPEG này tới `POST /recognize` để nhận OCR. Ảnh đã chụp xuất hiện
trước biển OCR, kể cả khi OCR chậm hoặc thất bại.
Với JPEG lấy từ `/snapshot`, backend gửi `image_is_lane_crop=true` để
AI không áp ROI lần thứ hai; ảnh tải lên từ nguồn khác giữ mặc định `false`.
OCR trong luồng cổng chỉ chạy model nhanh trên vùng biển tìm được; nếu không
đọc chắc chắn, nhân viên xác nhận từ ảnh. Backend giới hạn thời gian mỗi
yêu cầu AI còn 12 giây để lượt xe không treo một phút.

Trên dashboard thật, nhân viên tạo/chọn phiên, nhập mã phiên tại camera
làn tương ứng và bấm **Chụp**. Trình duyệt gọi
`POST /api/v1/alpr/capture` bằng Bearer JWT; backend kiểm tra phiên đang
ở `ENTRY_PENDING` cho làn vào hoặc `ACTIVE` cho làn ra, lưu URL ảnh trước,
rồi cập nhật `entry_ocr_plate`/`exit_ocr_plate` khi OCR xong. Nhân viên đối chiếu
ảnh, xác nhận biển để gửi lệnh cổng hoặc chuẩn bị lượt ra.

Payload:

```json
{"lane":"IN","session_id":"SES_1234ABCD"}
```

Backend tự sinh `request_id`, kiểm tra `request_id`, `lane`,
`session_id` trong phản hồi AI. Kết quả gồm `plate_number`,
`suggested_plate` nếu có, `needs_confirmation`, `image_url` và
`confidence`. Kết quả thiếu/chưa chắc chắn không tự mở cổng. Biển OCR
gốc được giữ riêng với biển nhân viên xác nhận.

`GET /api/v1/alpr/images/{image_id}` yêu cầu Bearer JWT. Web fetch ảnh
rồi dùng blob URL; không gắn token trong query string. Endpoint
`/alpr/recognize` còn giữ để tích hợp cũ, nhưng dashboard thật sử dụng
`/capture` và không mở camera từ `getUserMedia`.

## Giới hạn kiểm chứng

`simulator/mock_alpr.py` chỉ phục vụ nghiệm thu phần mềm, tạo ảnh có nhãn
`DEMO - OCR MOCK, NOT CAMERA`; không chứng minh độ chính xác OCR thật.
Hiện chưa có bộ 100 ảnh cùng commit để xác nhận NF02 hoặc tỷ lệ 94/100.
Khi lắp mô hình, cần kiểm chứng cả hai ROI nhìn rõ biển và thu log/ảnh
thật trước khi ghi kết quả vào báo cáo.
