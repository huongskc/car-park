USE `smart_parking_db`;

SET FOREIGN_KEY_CHECKS = 0;

TRUNCATE TABLE `audit_logs`;
TRUNCATE TABLE `alerts`;
TRUNCATE TABLE `telemetry_logs`;
TRUNCATE TABLE `commands`;
TRUNCATE TABLE `payments`;
TRUNCATE TABLE `parking_sessions`;
TRUNCATE TABLE `tariffs`;
TRUNCATE TABLE `cards`;
TRUNCATE TABLE `slots`;
TRUNCATE TABLE `device_configs`;
TRUNCATE TABLE `devices`;
TRUNCATE TABLE `users`;

SET FOREIGN_KEY_CHECKS = 1;

INSERT INTO `users` (`id`, `username`, `password_hash`, `full_name`, `email`, `role`, `is_active`) VALUES
(1, 'admin', '$2b$12$WhLeOSEXA3bV022NeEnauuQkd3zXmbyeCBCN96rIPEx1730mJR4ei', 'Quản trị viên Hệ thống', 'admin@ptit.edu.vn', 'ADMIN', 1),
(2, 'operator', '$2b$12$p..ulD5aKpApbZYnU/jfPuw6rb.9gG02EE4UjVjaQgTP5FNWFbtRy', 'Nhân viên Trực bãi', 'operator@ptit.edu.vn', 'OPERATOR', 1),
(3, 'viewer', '$2b$12$.Ea83ugyxt2/ZIrRKwL7/.OquZamhj3OJr8NMCfVFIWsI/xp2Tm36', 'Người xem Giám sát', 'viewer@ptit.edu.vn', 'VIEWER', 1);

INSERT INTO `devices` (`id`, `name`, `site_id`, `auth_token`, `status`, `is_online`, `ip_address`, `fw_version`, `hw_version`) VALUES
('ESP32_GATE_01', 'Bộ điều khiển Cổng Vào/Ra & Ô đỗ A1', 'PTIT_ZONE_1', 'token_esp32_secret_ptit_2026', 'ACTIVATED', 0, '192.168.137.50', '1.0.0', 'ESP32-WROOM-32');

INSERT INTO `device_configs` (`device_id`, `distance_threshold_cm`, `servo_open_time_sec`, `heartbeat_interval_sec`, `buzzer_enabled`, `version`) VALUES
('ESP32_GATE_01', 15.00, 10, 10, 1, 1);

INSERT INTO `slots` (`id`, `name`, `site_id`, `device_id`, `status`, `last_distance_cm`) VALUES
('SLOT_A1', 'Ô đỗ Ô tô Số 1 (Làn chính)', 'PTIT_ZONE_1', 'ESP32_GATE_01', 'EMPTY', 50.00);

INSERT INTO `cards` (`uid`, `card_type`, `owner_name`, `registered_plate`, `is_active`) VALUES
('A1B2C3D4', 'VISITOR', 'Khách vãng lai 01', NULL, 1),
('E5F6G7H8', 'MONTHLY', 'Nguyễn Văn Hưởng', '30A-999.88', 1),
('F9E8D7C6', 'VISITOR', 'Khách vãng lai 02 (Thẻ bị khóa)', NULL, 0);

INSERT INTO `tariffs` (`id`, `name`, `fee_type`, `flat_rate`, `hourly_rate`, `grace_period_mins`, `is_active`) VALUES
(1, 'Vé lượt Ô tô Tiêu chuẩn (20k/lượt)', 'FLAT', 20000.00, 0.00, 5, 1);

INSERT INTO `audit_logs` (`user_id`, `action`, `resource_type`, `resource_id`, `details`, `ip_address`) VALUES
(1, 'SYSTEM_INITIALIZE', 'DATABASE', 'smart_parking_db', 'Khởi tạo thành công CSDL và nạp dữ liệu mẫu ban đầu cho đồ án PTIT IoT Nhóm 08', '127.0.0.1');
