# Luồng demo Smart Parking

Khởi tạo **database mới** từ thư mục gốc repo:

```powershell
mysql -u root -p -e "source database/schema.sql"
mysql -u root -p -e "source database/seed.sql"
```

`schema.sql` tạo `smart_parking_db` với cấu trúc hiện tại; `seed.sql`
nạp tài khoản, thiết bị, thẻ và biểu phí demo. Hai file đặt lại bảng/dữ liệu
nếu chạy lại, nên chỉ dùng với DB mới hoặc DB demo dùng một lần.

## Lượt vào

RFID `GATE_IN` hoặc `POST /api/v1/sessions` tạo phiên `ENTRY_PENDING`
và giữ một ô `EMPTY` trong cùng site. RFID giữ ô trước khi gọi ALPR để thẻ
sai hoặc bãi đầy không phải chờ camera. AI service sở hữu webcam laptop,
chụp ROI_IN; dashboard chỉ gọi `POST /alpr/capture` sau khi có mã phiên.
Ảnh OCR gốc lưu ở `entry_ocr_plate`; biển nhân viên xác nhận lưu ở
`entry_plate`. Kết quả thiếu/chưa chắc chắn cần nhân viên đối chiếu ảnh
và gọi `PATCH /sessions/{id}/entry-plate`. Thẻ tháng luôn phải khớp biển
đăng ký, kể cả thẻ đã hết hạn.

Lệnh `OPEN` gửi đến ESP32 với `autoCloseMs=30000`. ACK thành công chỉ
chuyển sang `ENTRY_ACKED_WAITING_PASS`; Operator nhìn thấy xe qua rồi gọi
`POST /sessions/{id}/confirm-entry`. Khi đó phiên `ACTIVE` và ghi
`entry_time`. Lệnh thất bại có thể thử lại. Có thể hủy trước khi xe qua,
nhưng không hủy khi lệnh còn `SENT`; nếu ô có xe hoặc `UNKNOWN`, cần xác nhận
xe đã rời. LCD nhận mã từ chối qua topic `feedback`.

## Ô đỗ và lượt ra

Hai mẫu hợp lệ liên tiếp xác định `OCCUPIED` hoặc trống. Mẫu `≤2 cm`
bị bỏ qua; mẫu cũ không ghi đè mẫu mới. Quá 30 giây không có mẫu hợp lệ,
ô thành `UNKNOWN` và không được cấp. Phiên mở vẫn giữ quyền cấp riêng
kể cả lúc cảm biến báo trống.

RFID `GATE_OUT` hoặc `POST /sessions/{id}/prepare-exit` chụp ROI_OUT
và đối chiếu biển. Khớp chuyển `EXIT_READY`; lệch/thiếu/chưa chắc chắn
chuyển `PENDING_REVIEW`. Duyệt phải có biển nhân viên xác nhận và lý do.
Vé lượt cần nhân viên xác nhận tiền mặt hoặc MoMo giả lập. Thẻ tháng còn
hạn phí 0đ tự gửi lệnh. Mỗi phiên tối đa một payment; số tiền chốt tại
lúc vào theo biểu phí `FLAT` đang có hiệu lực và không đổi.

ACK cổng ra chuyển sang `EXIT_ACKED_WAITING_PASS`, chưa ghi giờ ra.
Operator thấy xe qua rồi gọi `POST /sessions/{id}/confirm-exit`; phiên
thành `COMPLETED`. Ô chỉ `EMPTY` nếu đã có hai mẫu trống hợp lệ và mẫu
cuối trong 30 giây, hoặc nhân viên kiểm tra ô rồi cập nhật thủ công với lý do.
Nếu xe quay đầu trước khi ra, `POST /sessions/{id}/cancel-exit` đưa phiên
về `ACTIVE`, giữ payment để lượt ra tiếp không thu lại. Các lệnh thử lại
có `commandId` mới; ACK trùng của một lệnh được bỏ qua.

## Điều khiển trực tiếp

Operator có thể gửi `OPEN`, `CLOSE`, `HOLD_OPEN`, `RESUME_AUTO` với lý do
và audit. Các lệnh này không tự thay đổi phiên. Chế độ tự động tự đóng sau
30 giây ở cả hai cổng theo hợp đồng firmware; ACK chỉ chứng minh ESP32
đã chấp nhận lệnh, không chứng minh góc servo hoặc xe qua.

Một ESP32 điều khiển hai servo và một webcam dùng hai ROI; một site chung.
`HOURLY` chưa tính. Firmware, kiểm chứng ROI trên mô hình thật và số liệu
ALPR 100 ảnh là việc tích hợp sau.
