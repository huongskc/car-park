# Tài liệu REST API Backend — Smart Parking IoT

> Luồng nghiệp vụ và trạng thái phiên sau migration được mô tả trong
> [business_flow.md](business_flow.md).

Tích hợp nhận diện biển số: xem [ALPR API](alpr_integration.md) để chụp và lấy
ảnh qua backend bằng JWT, không đưa khóa AI service xuống trình duyệt.

- **Base URL**: `http://localhost:8000/api/v1`  
- **OpenAPI Docs (Swagger UI)**: `http://localhost:8000/docs`  

---

## 1. Cơ chế Xác thực & Phân quyền (Auth & RBAC)

Hệ thống sử dụng **JSON Web Token (JWT)** theo chuẩn HTTP Authorization Header:
```http
Authorization: Bearer <access_token>
```

### Phân cấp Quyền (RBAC Hierarchy)
- **`ADMIN`**: Toàn quyền cấu hình người dùng, thiết bị, phát lệnh can thiệp sâu (`REBOOT`, `UPDATE_CONFIG`), tra cứu audit log.
- **`OPERATOR`**: Vận hành bãi xe, phát lệnh đóng/mở barrier (`OPEN`, `CLOSE`), duyệt ngoại lệ biển số, checkout phiên xe, xử lý cảnh báo sự cố.
- **`VIEWER`**: Chỉ xem dữ liệu realtime ô đỗ xe, không được sửa đổi dữ liệu.

---

## 2. Chi tiết Từng Phân hệ & Ví dụ cURL Thực tế

### 2.1 Phân hệ Xác thực (`/auth`)

#### `POST /auth/login` — Đăng nhập
- **Headers**: `Content-Type: application/json`
- **Body**:
  ```json
  {
    "username": "operator",
    "password": "op123"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "role": "OPERATOR",
    "username": "operator",
    "full_name": "Nhân viên Trực Cổng",
    "user_id": 2
  }
  ```
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"admin\", \"password\": \"admin123\"}"
  ```

#### `GET /auth/me` — Thông tin người dùng hiện tại
- **Headers**: `Authorization: Bearer <token>`
- **Response `200 OK`**:
  ```json
  {
    "id": 1,
    "username": "admin",
    "email": "admin@ptit.edu.vn",
    "full_name": "Quản trị viên Hệ thống",
    "role": "ADMIN",
    "is_active": true,
    "created_at": "2026-10-15T00:00:00"
  }
  ```
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/auth/me" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `PATCH /auth/me` — Cập nhật hồ sơ của chính mình
- **Body**: `{"full_name":"Nguyễn Văn A","email":"a@example.com"}`. Có thể gửi một trong hai trường; email trùng tài khoản khác bị từ chối.
- **Quyền**: Người dùng đã đăng nhập, không đổi vai trò hay tên đăng nhập.

---

### 2.2 Phân hệ Quản trị Người dùng (`/users`) — Quyền: `ADMIN`

#### `GET /users` — Danh sách người dùng
- **Query Params**: `skip` (mặc định 0), `limit` (mặc định 50), `role` (ADMIN, OPERATOR, VIEWER), `search` (chuỗi tìm kiếm).
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/users?skip=0&limit=10&role=OPERATOR" \
    -H "Authorization: Bearer $ADMIN_TOKEN"
  ```

#### `POST /users` — Tạo tài khoản mới
- **Body**:
  ```json
  {
    "username": "operator_ca1",
    "email": "ca1@parking.ptit.edu.vn",
    "password": "Password@123",
    "full_name": "Lê Văn Ca 1",
    "role": "OPERATOR"
  }
  ```
- **Response `201 Created`**: Trả về `UserOut`.
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/users" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"operator_ca1\", \"email\": \"ca1@ptit.edu.vn\", \"password\": \"Password@123\", \"full_name\": \"Lê Văn Ca 1\", \"role\": \"OPERATOR\"}"
  ```

#### `PATCH /users/{id}/status` — Khóa / Kích hoạt tài khoản
- **Quy tắc an toàn**: Chặn tự khóa chính tài khoản Admin đang gửi request (Self-lockout Protection).
- **Body**:
  ```json
  {
    "is_active": false
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/users/3/status" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"is_active\": false}"
  ```

---

### 2.3 Phân hệ Quản lý Thẻ RFID (`/cards`) — Quyền: `OPERATOR`, `ADMIN`

#### `GET /cards` — Danh sách thẻ
- **Query Params**: `skip`, `limit`, `card_type` (MONTHLY, VISITOR), `status` (ACTIVE, BLOCKED, EXPIRED), `search`.
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/cards?status=ACTIVE&search=30A" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `POST /cards` — Đăng ký thẻ mới
- **Body**:
  ```json
  {
    "uid": "E28011052000",
    "card_type": "MONTHLY",
    "registered_plate": "30A-999.88",
    "owner_name": "Nguyễn Văn Hưởng"
  }
  ```
- **Response `201 Created`**
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/cards" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"uid\": \"E28011052000\", \"card_type\": \"MONTHLY\", \"registered_plate\": \"30A-999.88\", \"owner_name\": \"Nguyễn Văn Hưởng\"}"
  ```

#### `PATCH /cards/{uid}/status` — Khóa hoặc Mở khóa thẻ
- **Body**:
  ```json
  {
    "is_active": false
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/cards/E28011052000/status" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"is_active\": false}"
  ```

---

### 2.4 Phân hệ Giám sát Ô đỗ xe Realtime (`/slots`) — Quyền: `VIEWER`, `OPERATOR`, `ADMIN`

#### `GET /slots` — Danh sách ô đỗ
- **Query Params**: `skip` (mặc định 0), `limit` (mặc định 50), `site_id`, `status` (`EMPTY`, `OCCUPIED`, `RESERVED`, `MAINTENANCE`).
- **Response `200 OK`**:
  ```json
  [
    {
      "id": "SLOT_A1",
      "site_id": "PTIT_ZONE_1",
      "slot_number": "A-01",
      "status": "EMPTY",
      "last_distance_cm": 45.2,
      "last_updated_at": "2026-10-17T15:30:00"
    }
  ]
  ```
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/slots?status=EMPTY&limit=20" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `PATCH /slots/{id}/status` — Cập nhật thủ công ô đỗ (OPERATOR & ADMIN)
- **Body**:
  ```json
  {
    "status": "MAINTENANCE",
    "reason": "Đã kiểm tra thực tế, cảm biến cần bảo trì"
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/slots/SLOT_A1/status" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"status\": \"MAINTENANCE\", \"reason\": \"Đã kiểm tra thực tế\"}"
  ```

#### `GET /slots/{id}/telemetry` — Xem lịch sử đo cảm biến siêu âm theo ô đỗ (VIEWER, OPERATOR, ADMIN)
- **Query Params**: `limit` (mặc định 40, tối đa 500), `minutes` (giới hạn N phút gần nhất, tối đa 1440).
- **Mô tả**: Trả về N bản ghi đo khoảng cách mới nhất từ cảm biến siêu âm của ô đỗ, được sắp xếp theo thứ tự thời gian tăng dần (`sampled_at.asc()`) để Frontend vẽ biểu đồ Sparkline trực tiếp. Trả về `404 Not Found` nếu không tìm thấy ô đỗ.
- **Response `200 OK`**:
  ```json
  [
    {
      "id": 105,
      "device_id": "ESP32_NODE_01",
      "slot_id": "SLOT_A1",
      "distance_cm": 15.4,
      "is_occupied": true,
      "boot_id": "boot_abc123",
      "seq": 42,
      "sampled_at": "2026-10-23T00:30:00",
      "received_at": "2026-10-23T00:30:01"
    }
  ]
  ```
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/slots/SLOT_A1/telemetry?limit=40" \
    -H "Authorization: Bearer $TOKEN"
  ```


---

### 2.5 Phân hệ Phiên gửi xe & Thu phí (`/sessions`) — Quyền: `OPERATOR`, `ADMIN`

`GET /sessions` hỗ trợ `skip`, `limit`, `status`, `search`; `GET /sessions/{id}`
trả chi tiết phiên. Các trạng thái mới là `ENTRY_PENDING`, `EXIT_READY`,
`PAID_WAITING_EXIT`. Các thao tác ghi dữ liệu đều yêu cầu Bearer token của
`OPERATOR` hoặc `ADMIN`.

| Thao tác | Body | Kết quả |
| --- | --- | --- |
| `POST /sessions` | `{"card_uid":"A1B2C3D4","device_id":"ESP32_GATE_01","entry_plate":null}` | Backend tự cấp ô `EMPTY`, tạo `ENTRY_PENDING` và giữ ô `RESERVED`. Có biển hợp lệ thì gửi lệnh cổng vào. |
| `PATCH /sessions/{id}/entry-plate` | `{"plate":"30A-123.45","note":"Đã xem ảnh"}` | Xác nhận hoặc sửa OCR; thẻ tháng phải khớp biển đăng ký. |
| `POST /sessions/{id}/prepare-exit` | `{"exit_plate":"30A-123.45"}` | Biển khớp thành `EXIT_READY`; thiếu/lệch thành `PENDING_REVIEW`. |
| `PATCH /sessions/{id}/resolve-plate` | `{"approved":true,"note":"Đã kiểm tra ảnh","actual_exit_plate":"30A-123.45"}` | Duyệt trực tiếp sang `EXIT_READY`; không cần quẹt lại. |
| `POST /sessions/{id}/checkout` | `{"payment_method":"CASH"}` | Chỉ nhận `EXIT_READY`; ghi một payment và trả `PAID_WAITING_EXIT` (HTTP 202). `SIMULATED_MOMO` chỉ là giả lập. |
| `POST /sessions/{id}/retry-gate` | Không cần body | Gửi lại lệnh khi lệnh cũ đã `FAILED` hoặc `EXPIRED`; không tạo thêm payment. |
| `POST /sessions/{id}/cancel-entry` | `{"reason":"Xe quay đầu","confirmed_departure":false}` | Hủy lượt vào đang chờ khi không còn lệnh `SENT`. Ô `OCCUPIED` cần `confirmed_departure=true` và vẫn chờ xác nhận trống trước khi cấp lại. |
| `POST /sessions/{id}/cancel` | `{"reason":"Xe đã rời sau xác minh","confirmed_exit":true}` | Hủy ngoại lệ phiên `PENDING_REVIEW` chưa thanh toán; ô vẫn chờ cảm biến báo trống hoặc nhân viên cập nhật sau kiểm tra. |

ACK cổng vào mới ghi `entry_time` và chuyển `ACTIVE`; ACK cổng ra mới ghi
`exit_time` và chuyển `COMPLETED`. Cảm biến báo ô trống sau lượt ra mới cho
cấp lại ô. Xem [business_flow.md](business_flow.md) để biết toàn bộ quy tắc.

---

### 2.6 Phân hệ Cảnh báo & Sự cố (`/alerts`) — Quyền: `OPERATOR`, `ADMIN`

#### `GET /alerts` — Danh sách cảnh báo
- **Query Params**: `skip`, `limit`, `status` (`NEW`, `ACKNOWLEDGED`, `RESOLVED`, `DISMISSED`), `severity` (`INFO`, `WARNING`, `CRITICAL`), `alert_type` (`PLATE_MISMATCH`, `UNAUTHORIZED_CARD`, `DEVICE_OFFLINE`, `LONG_OCCUPANCY`, `SYSTEM_ERROR`).
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/alerts?status=NEW&severity=WARNING" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `POST /alerts/{id}/ack` — Tiếp nhận sự cố
- **Chức năng**: Chuyển trạng thái từ `NEW` sang `ACKNOWLEDGED`, ghi nhận ID người tiếp nhận và tự động ghi vết kiểm toán vào `audit_logs` (`action="ACKNOWLEDGE_ALERT"`).
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/alerts/12/ack" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `PATCH /alerts/{id}/resolve` — Đóng cảnh báo sự cố
- **Chức năng**: Chuyển trạng thái sang `RESOLVED` hoặc `DISMISSED`, ghi nhận thời gian hoàn tất và tự động ghi vết kiểm toán vào `audit_logs` (`action="RESOLVE_ALERT"`).
- **Body**:
  ```json
  {
    "status": "RESOLVED"
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/alerts/12/resolve" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"status\": \"RESOLVED\"}"
  ```

---

### 2.7 Phân hệ Quản trị Thiết bị IoT (`/devices`)

#### `GET /devices` — Danh sách trạm ESP32 (OPERATOR & ADMIN)
- **Query Params**: `skip`, `limit`, `status` (`REGISTERED`, `ACTIVATED`, `MAINTENANCE`, `SUSPENDED`, `DECOMMISSIONED`), `search`.
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/devices" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `POST /devices` — Onboarding trạm mới (Chỉ ADMIN)
- **Body**:
  ```json
  {
    "id": "ESP32_GATE_01",
    "name": "Trạm Cổng Chính & Ô đỗ Zone 1",
    "site_id": "PTIT_ZONE_1",
    "ip_address": "192.168.1.50",
    "hw_version": "ESP32-WROOM-32",
    "fw_version": "1.0.0"
  }
  ```
- **Response `201 Created`**: Sinh mã token bí mật `auth_token: dtk_...` nạp vào vi điều khiển.
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/devices" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"id\": \"ESP32_GATE_01\", \"name\": \"Trạm Cổng 1\", \"site_id\": \"PTIT_ZONE_1\"}"
  ```

#### `PATCH /devices/{id}/status` — Chuyển trạng thái vòng đời (Chỉ ADMIN)
- **Body**:
  ```json
  {
    "status": "ACTIVATED",
    "reason": "Hoàn tất kiểm thử cơ khí và đấu nối cảm biến"
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/devices/ESP32_GATE_01/status" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"status\": \"ACTIVATED\", \"reason\": \"Kích hoạt vận hành chính thức\"}"
  ```

#### `PATCH /devices/{id}/config` — Cập nhật cấu hình từ xa (Chỉ ADMIN)
- **Nghiệp vụ**: Cập nhật CSDL, tự động tăng `version = version + 1`, và bắn bản tin MQTT `parking/{site_id}/{device_id}/config` đồng bộ xuống ESP32. Nếu publish MQTT thất bại, trả về HTTP `503 Service Unavailable`.
- **Body**:
  ```json
  {
    "distance_threshold_cm": 18.5,
    "servo_open_time_sec": 12,
    "heartbeat_interval_sec": 15,
    "buzzer_enabled": true
  }
  ```
- **cURL Example**:
  ```bash
  curl -X PATCH "http://localhost:8000/api/v1/devices/ESP32_GATE_01/config" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"distance_threshold_cm\": 18.5, \"servo_open_time_sec\": 12}"
  ```

#### `POST /devices/{id}/resend-config` — Gửi lại cấu hình qua MQTT (Chỉ ADMIN)
- **Nghiệp vụ**: Gửi lại bản tin cấu hình đã lưu trong CSDL xuống vi điều khiển ESP32 khi lần cập nhật trước bị lỗi kết nối Broker.
- **Response `200 OK`**: `{"status": "PUBLISHED", "device_id": "ESP32_GATE_01", "version": 2, "topic": "..."}`
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/devices/ESP32_GATE_01/resend-config" \
    -H "Authorization: Bearer $ADMIN_TOKEN"
  ```

---

### 2.8 Phân hệ Điều khiển Barrier & Phản hồi ACK (`/devices/{id}/commands`)

#### `POST /devices/{id}/commands` — Phát lệnh điều khiển phần cứng
- **Phân quyền hành động (`action`)**:
  - `OPEN` / `CLOSE`: Quyền **`OPERATOR`** và **`ADMIN`**.
  - `REBOOT` / `UPDATE_CONFIG`: Chỉ **`ADMIN`** (Risk-based RBAC).
- **Cơ chế an toàn (Safety Lock)**: Chặn phát lệnh nếu thiết bị `Offline`, `SUSPENDED`, hoặc `DECOMMISSIONED`.
- **Body**:
  ```json
  {
    "gate_id": "GATE_IN",
    "action": "OPEN",
    "reason": "Mở cổng thủ công đón xe cứu hỏa / xe ưu tiên"
  }
  ```
- **Response `201 Created`**:
  ```json
  {
    "id": "cmd_4fa7b89e12c4",
    "device_id": "ESP32_GATE_01",
    "gate_id": "GATE_IN",
    "action": "OPEN",
    "status": "SENT",
    "sent_by_user_id": 2,
    "ack_status": null,
    "ack_reason": null,
    "sent_at": "2026-10-17T15:35:00",
    "acked_at": null
  }
  ```
- **cURL Example**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/devices/ESP32_GATE_01/commands" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"gate_id\": \"GATE_IN\", \"action\": \"OPEN\", \"reason\": \"Mở cổng khẩn cấp\"}"
  ```

#### `GET /commands/{command_id}` — Tra cứu tiến độ nhận ACK từ phần cứng
- **Mô tả**: Dùng cho Frontend Polling hoặc kiểm tra sau khi phát lệnh để cập nhật trạng thái UI (`SENT` -> `ACKED` hoặc `FAILED`).
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/commands/cmd_4fa7b89e12c4" \
    -H "Authorization: Bearer $TOKEN"
  ```

---

### 2.9 Phân hệ Nhật ký Kiểm toán (`/audit-logs`) — Quyền: `ADMIN`

#### `GET /audit-logs` — Danh sách lịch sử tác động hệ thống
- **Query Params**: `skip`, `limit`, `user_id`, `action` (ví dụ: `BARRIER_OPEN`, `REGISTER_DEVICE`), `resource_type`.
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/audit-logs?action=BARRIER_OPEN&limit=20" \
    -H "Authorization: Bearer $ADMIN_TOKEN"
  ```

---

### 2.10 Phân hệ Biểu phí Gửi xe (`/tariffs`) — Quyền: `VIEWER`, `OPERATOR`, `ADMIN`

#### `GET /tariffs` — Danh sách biểu phí gửi xe
- **Query Params**: `skip` (mặc định 0), `limit` (mặc định 50), `is_active` (lọc theo trạng thái hiệu lực true/false).
- **Mô tả**: Tra cứu danh sách các biểu phí cấu hình trong hệ thống (vé lượt FLAT, vé theo giờ HOURLY).
- **Response `200 OK`**:
  ```json
  [
    {
      "id": 1,
      "name": "Vé lượt Ô tô Tiêu chuẩn",
      "fee_type": "FLAT",
      "flat_rate": 20000.0,
      "hourly_rate": 0.0,
      "grace_period_mins": 5,
      "is_active": true,
      "effective_from": "2026-01-01T00:00:00"
    }
  ]
  ```
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/tariffs?is_active=true" \
    -H "Authorization: Bearer $TOKEN"
  ```

#### `GET /tariffs/{id}` — Chi tiết một biểu phí
- **Response `200 OK`**: Thông tin chi tiết của biểu phí. Trả về `404 Not Found` nếu không tìm thấy.
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/tariffs/1" \
    -H "Authorization: Bearer $TOKEN"
  ```

---

### 2.11 Phân hệ Lịch sử Telemetry Tổng quát (`/telemetry`) — Quyền: `VIEWER`, `OPERATOR`, `ADMIN`

#### `GET /telemetry/history` — Lịch sử đo cảm biến siêu âm theo bộ lọc
- **Query Params**:
  - `slot_id`: Lọc theo mã ô đỗ xe (tùy chọn)
  - `device_id`: Lọc theo mã thiết bị ESP32 (tùy chọn)
  - `limit`: Số lượng mẫu đo tối đa (mặc định 40, tối đa 500)
  - `minutes`: Giới hạn trong N phút gần nhất (tùy chọn, tối đa 1440)
- **Mô tả**: Trả về N bản ghi đo cảm biến mới nhất thỏa mãn bộ lọc, được sắp xếp theo chuỗi thời gian tăng dần (`sampled_at.asc()`).
- **Response `200 OK`**:
  ```json
  [
    {
      "id": 105,
      "device_id": "ESP32_NODE_01",
      "slot_id": "SLOT_A1",
      "distance_cm": 15.4,
      "is_occupied": true,
      "boot_id": "boot_abc123",
      "seq": 42,
      "sampled_at": "2026-10-23T00:30:00",
      "received_at": "2026-10-23T00:30:01"
    }
  ]
  ```
- **cURL Example**:
  ```bash
  curl -X GET "http://localhost:8000/api/v1/telemetry/history?slot_id=SLOT_A1&limit=40" \
    -H "Authorization: Bearer $TOKEN"
  ```

---

## 3. Chuẩn Mã Lỗi HTTP & Định dạng Phản hồi

Toàn bộ các lỗi hệ thống đều trả về cấu trúc JSON đồng nhất:
```json
{
  "detail": "Mô tả nguyên nhân lỗi bằng tiếng Việt"
}
```

| HTTP Code | Ý nghĩa Kỹ thuật | Trường hợp Thường Gặp |
| :---: | :--- | :--- |
| **`400 Bad Request`** | Dữ liệu không hợp lệ hoặc vi phạm quy tắc an toàn | Trùng lặp ID thiết bị, thiết bị đang Offline khi phát lệnh, tự khóa tài khoản Admin chính mình. |
| **`401 Unauthorized`** | Sai thông tin đăng nhập hoặc thiếu token | Sai mật khẩu, token hết hạn hoặc sai chữ ký. |
| **`403 Forbidden`** | Không đủ quyền hạn (RBAC) | Viewer cố tạo thiết bị, Operator cố phát lệnh `REBOOT`. |
| **`404 Not Found`** | Tài nguyên không tồn tại | Không tìm thấy `device_id`, `card_uid`, `session_id`, `alert_id`. |
| **`422 Unprocessable`**| Sai kiểu dữ liệu JSON Schema | Sai tên trường, thiếu trường bắt buộc, giá trị Enum không nằm trong danh mục hợp lệ. |
