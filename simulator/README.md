# Demo phần mềm với MQTT và ALPR mock

Simulator chỉ dùng cho lab. `mock_alpr.py` trả biển số cấu hình sẵn và ảnh JPEG
có chữ **DEMO - OCR MOCK, NOT CAMERA**; không nhận diện ảnh thật.

Chạy nghiệm thu tự động từ gốc repo (cần MySQL, Docker, Chrome và hai venv):

```powershell
.\backend\.venv\Scripts\python.exe simulator\acceptance.py
```

Script chỉ tái tạo `smart_parking_demo`, chạy broker `eclipse-mosquitto:2.0.22`,
backend, ALPR mock, frontend và Chrome headless. Nó kiểm tra luồng MQTT thật,
thu hồi credential demo và dừng các tiến trình sau khi chạy. File mật khẩu và
log demo được Git bỏ qua. `smart_parking_db` không bị ghi.

Để dùng simulator tương tác, khởi động broker demo bằng
`deployment/demo_broker.py setup` sau khi đặt `DEMO_BACKEND_MQTT_PASSWORD` và
`SIM_MQTT_PASSWORD`; dùng `setup --reset` nếu cần sao lưu và tạo lại file demo.
Chạy kịch bản tự động một lần để tạo `smart_parking_demo` trước khi dùng tương
tác. Backend phải kết nối DB này và dùng cùng mật khẩu backend.
Tài khoản MQTT của simulator có username trùng `SIM_DEVICE_ID`; mật khẩu khác
token trong payload. Sau khi API chuyển thiết bị sang `SUSPENDED` hoặc
`DECOMMISSIONED`, đặt `DEMO_API_TOKEN` và chạy `deployment/demo_broker.py revoke`
để thu hồi credential trên broker demo.

1. Broker demo đã được lệnh `setup` khởi động theo `deployment/compose.demo.yml`.
2. Chạy backend đã cấu hình `ALPR_SERVICE_URL=http://127.0.0.1:8001` và
   `INTERNAL_ALPR_API_KEY` trùng `ALPR_API_KEY` của mock.
3. Trong PowerShell, đặt khóa mock rồi chạy từ gốc repo:

   ```powershell
   $env:ALPR_API_KEY = '<cùng khóa INTERNAL_ALPR_API_KEY>'
   .\ai-service\.venv\Scripts\python.exe -m uvicorn simulator.mock_alpr:app --host 127.0.0.1 --port 8001
   ```

4. Đặt thông tin tài khoản thiết bị demo và chạy terminal khác:

   ```powershell
   $env:SIM_DEVICE_ID = 'ESP32_GATE_01'
   $env:SIM_DEVICE_TOKEN = '<auth_token trong DB của thiết bị demo>'
   $env:SIM_MQTT_PASSWORD = '<mật khẩu MQTT của thiết bị demo>'
   .\backend\.venv\Scripts\python.exe simulator\device.py
   ```

5. Gõ `empty` hai lần để xác nhận ô trống ban đầu, rồi `entry`. Sau ACK
   cổng vào, Operator bấm **Đã thấy xe vào**. Gõ `occupied` hai lần, rồi
   `exit`, xác nhận thu tiền trên dashboard, bấm **Đã thấy xe ra** sau ACK,
   và gõ `empty` hai lần. Lệnh `fail` khiến ACK kế tiếp là
   `FAILED`, dùng để thử `retry-gate`.

Muốn đổi biển OCR mock, gọi `POST http://127.0.0.1:8001/scenario` với header
`X-API-Key` và JSON `{"entry_plate":"30A12345","exit_plate":"29B99999"}`.
Đặt `entry_needs_confirmation:true` hoặc `exit_needs_confirmation:true`
để mô phỏng OCR chưa chắc chắn và kiểm tra nhánh nhân viên duyệt.
Đặt `null` cho một biển để mô phỏng camera không đọc được. Chỉ điều chỉnh
scenario trong môi trường demo; AI service thật ở `ai-service/main.py` không bị
thay đổi.

Test ghi dữ liệu dùng `smart_parking_test` hoặc `smart_parking_demo`.
Để khởi tạo một DB mới cho ứng dụng, chạy `database/schema.sql` rồi
`database/seed.sql` một lần. Hai file này đặt lại dữ liệu, vì vậy chỉ dùng
với DB mới hoặc DB dùng một lần.
