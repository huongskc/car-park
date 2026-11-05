-- Additive upgrade from the original schema.sql. Run once after a verified backup.
-- Do not run seed.sql: it truncates project data. Existing sessions and tokens are preserved.
-- MySQL DDL commits implicitly; inspect SHOW COLUMNS before rerunning this one-time script.
USE `smart_parking_db`;

ALTER TABLE `cards`
  ADD COLUMN `valid_from` DATE NULL AFTER `registered_plate`,
  ADD COLUMN `valid_until` DATE NULL AFTER `valid_from`;

-- Existing monthly cards receive 30 days of validity starting on migration day.
UPDATE `cards` SET `valid_from` = CURRENT_DATE(),
                   `valid_until` = DATE_ADD(CURRENT_DATE(), INTERVAL 29 DAY)
WHERE `card_type` = 'MONTHLY' AND `valid_from` IS NULL AND `valid_until` IS NULL;

ALTER TABLE `parking_sessions`
  MODIFY COLUMN `status` ENUM('ENTRY_PENDING', 'ACTIVE', 'EXIT_READY', 'PENDING_REVIEW',
                              'PAID_WAITING_EXIT', 'COMPLETED', 'CANCELLED')
    NOT NULL DEFAULT 'ENTRY_PENDING',
  MODIFY COLUMN `entry_time` DATETIME NULL DEFAULT NULL,
  ADD COLUMN `device_id` VARCHAR(50) NULL AFTER `slot_id`,
  ADD CONSTRAINT `fk_sessions_device` FOREIGN KEY (`device_id`)
    REFERENCES `devices`(`id`) ON DELETE SET NULL;

ALTER TABLE `commands`
  ADD COLUMN `session_id` INT NULL AFTER `device_id`,
  ADD INDEX `idx_commands_session` (`session_id`),
  ADD CONSTRAINT `fk_commands_session` FOREIGN KEY (`session_id`)
    REFERENCES `parking_sessions`(`id`) ON DELETE SET NULL;
