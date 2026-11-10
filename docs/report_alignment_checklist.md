# Danh sách nhóm cần sửa trong IOT_Nhom08.docx

Tài liệu này là bàn giao sai lệch; không chỉnh DOCX trong đợt phần mềm.

1. Mô tả ACK `OPEN` chỉ đưa phiên đến
   `ENTRY_ACKED_WAITING_PASS`/`EXIT_ACKED_WAITING_PASS`. Operator xác nhận
   xe qua bằng `confirm-entry`/`confirm-exit` mới ghi `entry_time`/
   `exit_time`, chuyển `ACTIVE`/`COMPLETED`.
2. Cả hai cổng tự đóng **30 giây** trong chế độ tự động. `HOLD_OPEN` giữ mở
   đến `RESUME_AUTO`; ACK không chứng minh góc servo hoặc xe đã đi qua.
3. Một ESP32 điều khiển hai cổng; một webcam laptop do AI service sở hữu,
   chọn `ROI_IN`/`ROI_OUT`. Web gọi backend `/alpr/capture`, không mở
   `getUserMedia` trong luồng thật. Phải kiểm chứng hai ROI trên mô hình thật.
4. Cập nhật bảng API theo các endpoint đang chạy, gồm
   `/sessions/{id}/confirm-entry`, `confirm-exit`, `cancel-exit`,
   `/auth/me/change-password`, `/tariffs`, `/payments` và
   `/devices/{id}/commands` với `HOLD_OPEN`/`RESUME_AUTO`. Không thêm
   alias theo bảng API dự kiến.
5. Phân biệt: phần mềm + simulator đã nghiệm thu trên DB demo, firmware
   và số đo phần cứng thật đang do thành viên khác tích hợp. Không gọi
   ACK giả lập là bằng chứng servo thật đã chạy.
6. Bỏ các kết quả **94/100**, **74/90** và “100% dữ liệu đo thực tế” cho
   đến khi có ảnh/log gốc tương ứng cùng commit. Bộ 100 ảnh ALPR chưa có;
   NF02 ghi **chưa kiểm chứng**. Chương 4 phải thống nhất các ca đã chạy,
   chưa chạy và nguồn bằng chứng.

Lệnh kiểm chứng phần mềm: `backend/.venv/Scripts/python.exe -m pytest
backend/tests -q` trên `smart_parking_test`, và
`backend/.venv/Scripts/python.exe simulator/acceptance.py` trên
`smart_parking_demo` với Docker. Không dùng kết quả này để thay số liệu
ALPR thật.
