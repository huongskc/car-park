# Đặc tả Giao thức IoT & MQTT — Smart Parking System

- **Giao vận**: MQTT v3.1.1 qua TCP (Port 1883)  
- **Base Topic**: `parking`  

---

## 1. Thông số Kết nối Mạng & Mosquitto Broker

| Tham số | Giá trị Cấu hình | Ghi chú kỹ thuật |
| :--- | :--- | :--- |
| **MQTT Broker Host** | `localhost` hoặc IP Server Backend trong mạng LAN (VD: `192.168.1.100`) | ESP32 và máy chủ Backend phải cùng lớp mạng Wi-Fi |
| **Port** | `1883` | TCP chuẩn (Không mã hóa SSL trong môi trường Lab) |
| **Client ID** | `ESP32_{DEVICE_ID}` (VD: `ESP32_GATE_01`) | Khuyến nghị duy nhất trên toàn mạng Broker |
| **Keep Alive** | `30` giây | Chu kỳ ping kiểm tra kết nối tầng giao vận MQTT |
| **LWT (Last Will & Testament)** | Bắt buộc cấu hình khi gọi `connect()` | Broker tự động bắn khi ESP32 mất nguồn hoặc đứt Wi-Fi |
| **LWT Topic** | `parking/{site_id}/{device_id}/status` | Ví dụ: `parking/PTIT_ZONE_1/ESP32_GATE_01/status` |
| **LWT Payload** | `{"status": "offline"}` | Báo cho Backend cập nhật ngay `is_online = False` |
| **LWT QoS / Retain** | `QoS 1`, `Retain = False` | Đảm bảo chuyển giao tin cậy |

---

## 2. Quy ước Cấu trúc Topic (Topic Hierarchy & Templates)

Hệ thống Backend hỗ trợ 2 định dạng topic linh hoạt:
1. **Chuẩn Phân vùng 3 cấp (Khuyến nghị cho Production)**:
   ```text
   parking/{site_id}/{device_id}/{stream_or_action}
   ```
   *Ví dụ*: `parking/PTIT_ZONE_1/ESP32_GATE_01/telemetry`

2. **Chuẩn Rút gọn 2 cấp (Hỗ trợ cấu hình nhanh)**:
   ```text
   parking/{device_id}/{stream_or_action}
   ```
   *Ví dụ*: `parking/ESP32_GATE_01/status`

> [!IMPORTANT]
> - `{site_id}`: Phân vùng bãi đỗ xe (mặc định trong Lab: `PTIT_ZONE_1`).
> - `{device_id}`: Mã định danh trạm vi điều khiển (VD: `ESP32_GATE_01`).
> - Vi điều khiển bắt buộc phải được Admin kích hoạt sang trạng thái **`ACTIVATED`** trên Backend thì các bản tin `telemetry` và `events/rfid` mới được tiếp nhận vào luồng xử lý.

---

## 3. Đặc tả Bản tin Inbound (ESP32 $\rightarrow$ Backend)

### 3.1 Bản tin Trạng thái & Nhịp tim (`status`)
- **Mục đích**: Thông báo thiết bị đang hoạt động, cập nhật IP động và phiên bản firmware.
- **Tần suất**: Định kỳ mỗi `10` giây (hoặc giá trị `heartbeatIntervalSec` nhận từ cấu hình).
- **Topic**: `parking/{site_id}/{device_id}/status`
- **QoS**: `0` hoặc `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "status": "online",
    "fwVersion": "1.0.0",
    "ipAddress": "192.168.1.50"
  }
  ```
- **Diễn giải trường**:
  - `status`: Bắt buộc. `"online"` (hoặc `"offline"` trong bản tin LWT).
  - `fwVersion`: Chuỗi phiên bản phần mềm (VD: `"1.0.0"`).
  - `ipAddress`: Địa chỉ IP cấp phát qua DHCP để hỗ trợ quản trị từ xa.

---

### 3.2 Bản tin Đo khoảng cách Siêu âm Ô đỗ (`telemetry`)
- **Mục đích**: Báo cáo khoảng cách đo được từ cảm biến siêu âm HC-SR04 tại vị trí ô đỗ xe.
- **Tần suất**: Gửi khi khoảng cách thay đổi $\ge 5\text{ cm}$ hoặc định kỳ mỗi `5` giây.
- **Topic**: `parking/{site_id}/{device_id}/telemetry`
- **QoS**: `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "slotId": "SLOT_A1",
    "distanceCm": 14.5,
    "bootId": "boot_101",
    "seq": 142,
    "sampledAt": "2026-10-17T15:30:00Z"
  }
  ```
- **Diễn giải trường**:
  - `slotId`: Mã định danh ô đỗ xe trong cơ sở dữ liệu (VD: `"SLOT_A1"`).
  - `distanceCm`: Khoảng cách đo bằng centimet (số thực float, làm tròn 2 chữ số).
  - `bootId`: Mã phiên khởi động của ESP32 (tuỳ chọn, phục vụ theo dõi watchdog).
  - `seq`: Số thứ tự gói tin tăng dần (phục vụ thống kê tỷ lệ rớt gói).
  - `sampledAt`: Mốc thời gian ISO-8601 (tuỳ chọn, Backend tự gán mốc hiện tại nếu thiếu).

> [!TIP]
> **Bộ lọc khử nhiễu cảm biến siêu âm**:  
> Backend tự động bỏ qua các giá trị $\le 2.0\text{ cm}$ (giá trị dội âm lỗi hoặc đứt dây tín hiệu thường trả về `0.0 cm`). Nếu khoảng cách nằm trong khoảng $(2.0\text{ cm}, \text{threshold}]$, ô đỗ được đánh giá là `OCCUPIED`. Ngược lại là `EMPTY`.

---

### 3.3 Bản tin Sự kiện Quẹt thẻ RFID (`events/rfid`)
- **Mục đích**: Gửi dữ liệu mã thẻ đọc được từ đầu đọc RFID RC522 tại Barrier vào/ra.
- **Tần suất**: Bắn tức thời ngay khi người dùng quẹt thẻ.
- **Topic**: `parking/{site_id}/{device_id}/events/rfid`
- **QoS**: `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "cardUid": "A1B2C3D4",
    "gate": "GATE_IN",
    "readAt": "2026-10-17T15:30:05Z"
  }
  ```
- **Diễn giải trường**:
  - `cardUid`: Chuỗi Hex UID in hoa không có dấu cách (VD: `"A1B2C3D4"` hoặc `"E28011052000"`).
  - `gate`: Mã cổng quẹt thẻ, nhận 1 trong 2 giá trị: `"GATE_IN"` (cổng vào) hoặc `"GATE_OUT"` (cổng ra).
  - `readAt`: Mốc thời gian ISO-8601 tại lúc quẹt thẻ.

---

### 3.4 Bản tin Xác nhận Thực thi Cơ khí (`ack`)
- **Mục đích**: Báo cáo kết quả điều khiển Servo Barrier hoặc tác vụ phần cứng về Backend.
- **Tần suất**: Gửi ngay sau khi động cơ Servo đã hoàn tất góc quay $90^\circ$ hoặc gặp sự cố kẹt cơ.
- **Topic**: `parking/{site_id}/{device_id}/ack`
- **QoS**: `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "commandId": "cmd_4fa7b89e12c4",
    "gate": "GATE_IN",
    "status": "SUCCESS",
    "reason": "Barrier opened 90 deg successfully"
  }
  ```
- **Diễn giải trường**:
  - `commandId`: Bắt buộc. Mã lệnh nhận được từ bản tin `commands` của Backend.
  - `gate`: Cổng thực thi (`"GATE_IN"` hoặc `"GATE_OUT"`).
  - `status`: Bắt buộc. `"SUCCESS"` (thành công) hoặc `"FAILED"` (thất bại/kẹt cơ khí).
  - `reason`: Mô tả chi tiết kết quả cơ học hoặc mã lỗi phần cứng.

---

## 4. Đặc tả Bản tin Outbound (Backend $\rightarrow$ ESP32)

ESP32 cần đăng ký (`subscribe`) 2 topic dưới đây khi vừa kết nối thành công tới Mosquitto:
1. `parking/{site_id}/{device_id}/commands`
2. `parking/{site_id}/{device_id}/config`

---

### 4.1 Bản tin Lệnh Điều khiển Phần cứng (`commands`)
- **Mục đích**: Phát lệnh mở/đóng barrier từ hệ thống tự động hoặc nút bấm khẩn cấp từ Web Dashboard.
- **Topic**: `parking/{site_id}/{device_id}/commands`
- **QoS**: `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "commandId": "cmd_4fa7b89e12c4",
    "gate": "GATE_IN",
    "action": "OPEN",
    "reason": "Xác thực thẻ hợp lệ - Mở barrier đón xe",
    "sentAt": "2026-10-17T15:35:00.123456"
  }
  ```
- **Hành vi Vi điều khiển ứng với từng `action`**:
  - **`OPEN`**:
    1. Kích hoạt còi Buzzer (nếu `buzzerEnabled = true`).
    2. Điều khiển Servo cổng chỉ định (`gate`) quay từ $0^\circ \rightarrow 90^\circ$.
    3. Gửi bản tin `ack` với `status: "SUCCESS"`.
    4. Giữ mở trong khoảng thời gian `servoOpenTimeSec` (mặc định 10 giây).
    5. Điều khiển Servo quay trở về $0^\circ$ (đóng cổng an toàn).
  - **`CLOSE`**: Cưỡng bức đóng Servo về $0^\circ$ ngay lập tức, sau đó gửi `ack`.
  - **`REBOOT`**: Gửi `ack` thông báo chuẩn bị khởi động lại, sau đó gọi `ESP.restart()`.
  - **`UPDATE_CONFIG`**: Đọc lại thông số hoặc yêu cầu đồng bộ cấu hình mới.

---

### 4.2 Bản tin Đồng bộ Cấu hình Từ xa (`config`)
- **Mục đích**: Thay đổi ngưỡng cảm biến, thời gian trễ mở barrier và chu kỳ nhịp tim từ xa mà không cần nạp lại mã nguồn.
- **Topic**: `parking/{site_id}/{device_id}/config`
- **QoS**: `1`
- **Payload Schema (JSON)**:
  ```json
  {
    "version": 2,
    "distanceThresholdCm": 18.5,
    "servoOpenTimeSec": 12,
    "heartbeatIntervalSec": 10,
    "buzzerEnabled": true,
    "updatedAt": "2026-10-17T15:35:00.123456"
  }
  ```
- **Hành vi Vi điều khiển**:
  1. Kiểm tra trường `version`: Nếu `version > current_version` trong bộ nhớ, tiếp nhận cấu hình mới.
  2. Cập nhật các biến toàn cục trong RAM:
     - `distanceThresholdCm`: Ngưỡng nhận diện có xe đỗ.
     - `servoOpenTimeSec`: Thời gian giữ barrier mở trước khi tự hạ.
     - `heartbeatIntervalSec`: Chu kỳ gửi bản tin `status`.
     - `buzzerEnabled`: Bật/tắt còi cảnh báo quẹt thẻ và mở barrier.
  3. Ghi lưu cấu hình mới vào `Preferences` (NVS Flash) để bảo toàn khi mất nguồn.

---

## 5. Hướng dẫn Kỹ thuật Dành cho Embedded Lead (Best Practices)

1. **Non-blocking Loop**:
   - Tuyệt đối **không sử dụng `delay()`** trong hàm `loop()`.
   - Sử dụng biến mốc thời gian `millis()` để kiểm tra chu kỳ gửi Heartbeat và Telemetry, tránh làm nghẽn hàm xử lý callback của MQTT `client.loop()`.

2. **Cơ chế Tự phục hồi Kết nối (Auto-Reconnect)**:
   ```cpp
   void reconnectMQTT() {
       while (!client.connected()) {
           // Đăng ký kết nối kèm bản tin LWT chuẩn
           if (client.connect(DEVICE_ID, "parking/PTIT_ZONE_1/ESP32_GATE_01/status", 1, false, "{\"status\":\"offline\"}")) {
               // Đăng ký lại các topic điều khiển
               client.subscribe("parking/PTIT_ZONE_1/ESP32_GATE_01/commands");
               client.subscribe("parking/PTIT_ZONE_1/ESP32_GATE_01/config");
               // Bắn ngay bản tin online
               client.publish("parking/PTIT_ZONE_1/ESP32_GATE_01/status", "{\"status\":\"online\"}", 1);
           } else {
               delay(3000); // Đợi 3 giây trước khi thử lại
           }
       }
   }
   ```

3. **Sơ đồ Chân Phần cứng Tham khảo (ESP32 DevKit V1)**:
   - **Đầu đọc RFID-RC522 (Cổng vào)**: `SS: GPIO 5`, `RST: GPIO 22`, `SCK: GPIO 18`, `MOSI: GPIO 23`, `MISO: GPIO 19`.
   - **Cảm biến Siêu âm HC-SR04**: `Trig: GPIO 13`, `Echo: GPIO 12`.
   - **Động cơ Servo SG90 (Barrier vào)**: `Signal: GPIO 4` (PWM).
   - **Động cơ Servo SG90 (Barrier ra)**: `Signal: GPIO 2` (PWM).
   - **Còi Buzzer**: `Positive: GPIO 15`.
   - **Màn hình LCD 1602 (I2C)**: `SDA: GPIO 21`, `SCL: GPIO 22`.
