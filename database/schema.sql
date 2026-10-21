CREATE DATABASE IF NOT EXISTS `smart_parking_db` 
CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE `smart_parking_db`;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `full_name` VARCHAR(100) NOT NULL,
    `email` VARCHAR(100) NULL UNIQUE,
    `role` ENUM('ADMIN', 'OPERATOR', 'VIEWER') NOT NULL DEFAULT 'VIEWER',
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_users_role` (`role`),
    INDEX `idx_users_is_active` (`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `devices`;
CREATE TABLE `devices` (
    `id` VARCHAR(50) PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL,
    `site_id` VARCHAR(50) NOT NULL DEFAULT 'PTIT_ZONE_1',
    `auth_token` VARCHAR(255) NOT NULL,
    `status` ENUM('REGISTERED', 'ACTIVATED', 'MAINTENANCE', 'SUSPENDED', 'DECOMMISSIONED') NOT NULL DEFAULT 'REGISTERED',
    `is_online` BOOLEAN NOT NULL DEFAULT FALSE,
    `ip_address` VARCHAR(45) NULL,
    `fw_version` VARCHAR(20) NULL DEFAULT '1.0.0',
    `hw_version` VARCHAR(50) NULL DEFAULT 'ESP32-WROOM-32',
    `last_heartbeat_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_devices_status` (`status`),
    INDEX `idx_devices_site` (`site_id`),
    INDEX `idx_devices_online` (`is_online`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `device_configs`;
CREATE TABLE `device_configs` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `device_id` VARCHAR(50) NOT NULL UNIQUE,
    `distance_threshold_cm` DECIMAL(5,2) NOT NULL DEFAULT 15.00,
    `servo_open_time_sec` INT NOT NULL DEFAULT 10,
    `heartbeat_interval_sec` INT NOT NULL DEFAULT 10,
    `buzzer_enabled` BOOLEAN NOT NULL DEFAULT TRUE,
    `version` INT NOT NULL DEFAULT 1,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT `fk_configs_device` FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `slots`;
CREATE TABLE `slots` (
    `id` VARCHAR(20) PRIMARY KEY,
    `name` VARCHAR(50) NOT NULL,
    `site_id` VARCHAR(50) NOT NULL DEFAULT 'PTIT_ZONE_1',
    `device_id` VARCHAR(50) NULL,
    `status` ENUM('EMPTY', 'OCCUPIED', 'RESERVED', 'MAINTENANCE') NOT NULL DEFAULT 'EMPTY',
    `last_distance_cm` DECIMAL(5,2) NULL,
    `last_updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_slots_status` (`status`),
    INDEX `idx_slots_site` (`site_id`),
    CONSTRAINT `fk_slots_device` FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `cards`;
CREATE TABLE `cards` (
    `uid` VARCHAR(50) PRIMARY KEY,
    `card_type` ENUM('VISITOR', 'MONTHLY') NOT NULL DEFAULT 'VISITOR',
    `owner_name` VARCHAR(100) NULL,
    `registered_plate` VARCHAR(20) NULL,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_cards_type` (`card_type`),
    INDEX `idx_cards_active` (`is_active`),
    INDEX `idx_cards_plate` (`registered_plate`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `tariffs`;
CREATE TABLE `tariffs` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL,
    `fee_type` ENUM('FLAT', 'HOURLY') NOT NULL DEFAULT 'FLAT',
    `flat_rate` DECIMAL(10,2) NOT NULL DEFAULT 20000.00,
    `hourly_rate` DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    `grace_period_mins` INT NOT NULL DEFAULT 5,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `effective_from` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `parking_sessions`;
CREATE TABLE `parking_sessions` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `session_code` VARCHAR(50) NOT NULL UNIQUE,
    `card_uid` VARCHAR(50) NOT NULL,
    `slot_id` VARCHAR(20) NULL,
    `tariff_id` INT NOT NULL,
    `status` ENUM('ACTIVE', 'COMPLETED', 'PENDING_REVIEW', 'CANCELLED') NOT NULL DEFAULT 'ACTIVE',
    `entry_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `entry_plate` VARCHAR(20) NULL,
    `entry_image_url` VARCHAR(255) NULL,
    `exit_time` DATETIME NULL,
    `exit_plate` VARCHAR(20) NULL,
    `exit_image_url` VARCHAR(255) NULL,
    `fee_amount` DECIMAL(10,2) NOT NULL DEFAULT 20000.00,
    `is_paid` BOOLEAN NOT NULL DEFAULT FALSE,
    `review_reason` VARCHAR(255) NULL,
    `reviewed_by` INT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_sessions_card_uid` (`card_uid`),
    INDEX `idx_sessions_status` (`status`),
    INDEX `idx_sessions_entry_time` (`entry_time`),
    INDEX `idx_sessions_active_lookup` (`card_uid`, `status`),
    CONSTRAINT `fk_sessions_card` FOREIGN KEY (`card_uid`) REFERENCES `cards`(`uid`) ON DELETE RESTRICT,
    CONSTRAINT `fk_sessions_slot` FOREIGN KEY (`slot_id`) REFERENCES `slots`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_sessions_tariff` FOREIGN KEY (`tariff_id`) REFERENCES `tariffs`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_sessions_reviewer` FOREIGN KEY (`reviewed_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `payments`;
CREATE TABLE `payments` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `session_id` INT NOT NULL,
    `amount` DECIMAL(10,2) NOT NULL,
    `payment_method` ENUM('CASH', 'SIMULATED_MOMO', 'SYSTEM') NOT NULL DEFAULT 'CASH',
    `payment_status` ENUM('PAID', 'PENDING', 'REFUNDED') NOT NULL DEFAULT 'PAID',
    `transaction_code` VARCHAR(50) NULL UNIQUE,
    `paid_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `operator_id` INT NULL,
    UNIQUE KEY `uq_payments_session` (`session_id`),
    INDEX `idx_payments_session` (`session_id`),
    INDEX `idx_payments_paid_at` (`paid_at`),
    CONSTRAINT `fk_payments_session` FOREIGN KEY (`session_id`) REFERENCES `parking_sessions`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_payments_operator` FOREIGN KEY (`operator_id`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `commands`;
CREATE TABLE `commands` (
    `id` VARCHAR(50) PRIMARY KEY,
    `device_id` VARCHAR(50) NOT NULL,
    `gate_id` ENUM('GATE_IN', 'GATE_OUT') NOT NULL,
    `action` ENUM('OPEN', 'CLOSE', 'REBOOT', 'UPDATE_CONFIG') NOT NULL,
    `status` ENUM('PENDING', 'SENT', 'ACKED', 'FAILED', 'EXPIRED') NOT NULL DEFAULT 'PENDING',
    `sent_by_user_id` INT NULL,
    `ack_status` VARCHAR(50) NULL,
    `ack_reason` VARCHAR(255) NULL,
    `sent_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `acked_at` DATETIME NULL,
    INDEX `idx_commands_device_status` (`device_id`, `status`),
    INDEX `idx_commands_sent_at` (`sent_at`),
    CONSTRAINT `fk_commands_device` FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_commands_user` FOREIGN KEY (`sent_by_user_id`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `telemetry_logs`;
CREATE TABLE `telemetry_logs` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
    `device_id` VARCHAR(50) NOT NULL,
    `slot_id` VARCHAR(20) NOT NULL,
    `distance_cm` DECIMAL(5,2) NOT NULL,
    `is_occupied` BOOLEAN NOT NULL,
    `boot_id` VARCHAR(50) NULL,
    `seq` INT NULL,
    `sampled_at` DATETIME NOT NULL,
    `received_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_telemetry_device_time` (`device_id`, `sampled_at`),
    INDEX `idx_telemetry_slot_time` (`slot_id`, `sampled_at`),
    CONSTRAINT `fk_telemetry_device` FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `alerts`;
CREATE TABLE `alerts` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `alert_type` ENUM('PLATE_MISMATCH', 'UNAUTHORIZED_CARD', 'DEVICE_OFFLINE', 'LONG_OCCUPANCY', 'SYSTEM_ERROR') NOT NULL,
    `severity` ENUM('INFO', 'WARNING', 'CRITICAL') NOT NULL DEFAULT 'WARNING',
    `message` VARCHAR(255) NOT NULL,
    `device_id` VARCHAR(50) NULL,
    `session_id` INT NULL,
    `status` ENUM('NEW', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED') NOT NULL DEFAULT 'NEW',
    `resolved_by` INT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `resolved_at` DATETIME NULL,
    INDEX `idx_alerts_status` (`status`),
    INDEX `idx_alerts_severity` (`severity`),
    INDEX `idx_alerts_created_at` (`created_at`),
    CONSTRAINT `fk_alerts_device` FOREIGN KEY (`device_id`) REFERENCES `devices`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_alerts_session` FOREIGN KEY (`session_id`) REFERENCES `parking_sessions`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_alerts_user` FOREIGN KEY (`resolved_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `audit_logs`;
CREATE TABLE `audit_logs` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
    `user_id` INT NULL,
    `action` VARCHAR(100) NOT NULL,
    `resource_type` VARCHAR(50) NOT NULL,
    `resource_id` VARCHAR(50) NULL,
    `details` TEXT NULL,
    `ip_address` VARCHAR(45) NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_audit_user` (`user_id`),
    INDEX `idx_audit_action` (`action`),
    INDEX `idx_audit_created_at` (`created_at`),
    CONSTRAINT `fk_audit_user` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
