-- Migration: Create qr_verification_logs table for staff dashboard
-- This table tracks all QR code and booking reference verifications by staff

CREATE TABLE IF NOT EXISTS qr_verification_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id INT NOT NULL,
    scanned_by INT NOT NULL,
    scan_status ENUM('valid', 'invalid', 'expired', 'already_used') NOT NULL DEFAULT 'valid',
    cinema_id INT NULL,
    device_info VARCHAR(255) NULL,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_booking (booking_id),
    INDEX idx_scanned_by (scanned_by),
    INDEX idx_scanned_at (scanned_at),
    FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY (scanned_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_scan_status ON qr_verification_logs(scan_status);
CREATE INDEX IF NOT EXISTS idx_cinema_date ON qr_verification_logs(cinema_id, scanned_at);
