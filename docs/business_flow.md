# Luồng nghiệp vụ demo phần mềm

Áp dụng sau `database/migrate_business_flow.sql`. Migration chỉ chạy một lần sau
khi sao lưu DB; không chạy `database/seed.sql` trên DB project vì file đó xóa dữ liệu.

## Xe vào

RFID `GATE_IN` hoặc `POST /api/v1/sessions` (gồm `device_id`, `card_uid`,
`entry_plate`) dùng chung luồng. Backend chọn ô `EMPTY` đầu tiên trong cùng site,
chuyển sang `RESERVED` và tạo phiên `ENTRY_PENDING`. Không có biển số thì nhân
viên kiểm tra ảnh, nhập biển qua `PATCH /sessions/{id}/entry-plate` với `plate`
và `note`. Biển thẻ tháng phải khớp biển đăng ký sau khi sửa OCR; nếu không khớp,
cổng không mở. Chỉ ACK `GATE_IN/OPEN` thành công mới chuyển `ACTIVE` và ghi
`entry_time`. Lệnh lỗi/hết hạn có thể gửi lại qua `POST /sessions/{id}/retry-gate`;
hoặc hủy lượt chờ qua `POST /sessions/{id}/cancel-entry` với `reason`. Không hủy
khi lệnh cổng còn `SENT`. Nếu ô báo `OCCUPIED`, nhân viên phải xác nhận xe đã
rời bằng `confirmed_departure=true`; ô vẫn chờ xác nhận trống.

## Ô đỗ và xe ra

Telemetry xác nhận ô đã cấp có xe bằng `OCCUPIED`; khi chưa có xe, ô vẫn
`RESERVED`. Xe đến cổng ra được ghi qua RFID `GATE_OUT` hoặc
`POST /sessions/{id}/prepare-exit` với `exit_plate`. Biển khớp chuyển
`EXIT_READY`; biển thiếu/lệch chuyển `PENDING_REVIEW`. Nhân viên xác minh và
duyệt bằng `PATCH /sessions/{id}/resolve-plate` với `approved`, `note`,
`actual_exit_plate`; không cần quẹt lại.

Vé lượt `EXIT_READY` cần nhân viên xác nhận đã nhận tiền qua
`POST /sessions/{id}/checkout` (`payment_method=CASH` hoặc
`SIMULATED_MOMO`). MoMo chỉ là mô phỏng, không gọi ví thật. Thẻ tháng còn hạn
được miễn phí và tự gửi lệnh mở cổng sau khi biển khớp. Payment được ghi một
lần, trạng thái `PAID_WAITING_EXIT` giữ nguyên nếu publish/ACK thất bại;
`retry-gate` không thu tiền lần hai. Chỉ ACK `GATE_OUT/OPEN` thành công mới ghi
`COMPLETED` và `exit_time`. ACK là mốc cho phép xe ra trong demo; chưa có cảm
biến xác nhận xe qua cổng. Ô chỉ thành `EMPTY` khi telemetry xác nhận trống,
hoặc nhân viên đã kiểm tra thực tế và cập nhật ô. Hủy ngoại lệ phiên
`PENDING_REVIEW` sau khi xe đã rời cũng giữ ô đến bước xác nhận trống này.

Nút mở barrier trực tiếp dành cho sự cố, bắt buộc nhập lý do và ghi audit.
Lệnh đó không tự đổi trạng thái phiên.

## Biểu phí và dữ liệu cũ

Chỉ hỗ trợ biểu phí `FLAT`: vé lượt dùng mức phí chốt lúc vào. Thẻ tháng có
`valid_from` và `valid_until` (bao gồm cả ngày cuối); hết hạn được tính như vé
lượt nhưng vẫn phải dùng đúng biển đăng ký. Nếu hết hạn khi xe đang đỗ, phí của
lượt đó không đổi. `HOURLY` hiện chỉ có trong schema, chưa được tính.

Migration giữ nguyên phiên/thanh toán cũ. Bản ghi `CANCELLED` nhưng `PAID`
đã tồn tại trước migration cần được nhóm đối soát riêng; migration không sửa nó.
