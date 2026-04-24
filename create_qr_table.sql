-- Run this in MySQL Workbench to create the missing table

USE tickit_db;

CREATE TABLE IF NOT EXISTS qr_verification_logs (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    booking_id          INT NOT NULL,
    scanned_by          INT NOT NULL,
    scanned_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scan_status         ENUM('valid','expired','already_used','invalid') NOT NULL,
    cinema_id           INT NULL,
    device_info         VARCHAR(255) NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY (scanned_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL,
    INDEX idx_booking_id (booking_id),
    INDEX idx_scanned_at (scanned_at)
);
