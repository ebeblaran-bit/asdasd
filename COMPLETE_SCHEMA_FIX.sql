-- ============================================================
--  TICK.IT — Complete Schema Fix
--  Run this AFTER tickit_db.sql to add all missing tables/columns
-- ============================================================

USE tickit_db;

-- ── 1. ADD role COLUMN TO users ──────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS role ENUM('customer','staff','admin')
    NOT NULL DEFAULT 'customer' AFTER password;

-- ── 2. STAFF PROFILES TABLE ──────────────────────────────────
CREATE TABLE IF NOT EXISTS staff_profiles (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL UNIQUE,
    employee_id VARCHAR(20) NOT NULL UNIQUE,
    cinema_id   INT NULL,
    shift_start TIME NULL,
    shift_end   TIME NULL,
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL,
    INDEX idx_employee_id (employee_id),
    INDEX idx_cinema_id (cinema_id)
);

-- ── 3. QR VERIFICATION LOGS TABLE ────────────────────────────
CREATE TABLE IF NOT EXISTS qr_verification_logs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    booking_id  INT NOT NULL,
    scanned_by  INT NOT NULL,
    scanned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scan_status ENUM('valid','expired','already_used','invalid') NOT NULL,
    cinema_id   INT NULL,
    device_info VARCHAR(255) NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY (scanned_by) REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (cinema_id)  REFERENCES cinemas(id)  ON DELETE SET NULL,
    INDEX idx_booking_id (booking_id),
    INDEX idx_scanned_by (scanned_by),
    INDEX idx_scanned_at (scanned_at)
);

-- ── 4. BOOKING STATUS HISTORY TABLE ──────────────────────────
CREATE TABLE IF NOT EXISTS booking_status_history (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    booking_id  INT NOT NULL,
    old_status  VARCHAR(30) NULL,
    new_status  VARCHAR(30) NOT NULL,
    changed_by  INT NULL,
    changed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason      TEXT NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id)    ON DELETE SET NULL,
    INDEX idx_booking_id (booking_id)
);

-- ── 5. ADD MISSING COLUMNS TO bookings ───────────────────────

-- booking_type: 'online' or 'walkin'
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS booking_type
        ENUM('online','walkin') NOT NULL DEFAULT 'online'
        AFTER status;

-- qr_code_data: JSON payload embedded in QR
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS qr_code_data TEXT NULL AFTER ref_code;

-- qr_image_path: path to saved QR image file
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS qr_image_path VARCHAR(500) NULL AFTER qr_code_data;

-- expiry_time: when the booking reservation expires
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS expiry_time DATETIME NULL AFTER created_at;

-- checked_in_at: timestamp of staff check-in
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS checked_in_at DATETIME NULL;

-- checked_in_by: staff user who performed check-in
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS checked_in_by INT NULL;

-- checked_in_cinema_id: cinema where check-in happened
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS checked_in_cinema_id INT NULL;

-- Add foreign keys for check-in columns (ignore if already exist)
ALTER TABLE bookings
    ADD CONSTRAINT IF NOT EXISTS fk_booking_checked_in_by
        FOREIGN KEY (checked_in_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE bookings
    ADD CONSTRAINT IF NOT EXISTS fk_booking_checked_in_cinema
        FOREIGN KEY (checked_in_cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL;

-- ── 6. FIX bookings.status ENUM ──────────────────────────────
-- The status column must support 'checked_in' and lowercase values.
-- First normalise existing data to lowercase:
UPDATE bookings SET status = LOWER(status)
    WHERE status IN ('Confirmed','Cancelled','Completed');

-- Now widen the enum:
ALTER TABLE bookings
    MODIFY COLUMN status
        ENUM('Pending','pending','Confirmed','confirmed','Cancelled','cancelled',
             'Completed','completed','checked_in','expired')
        NOT NULL DEFAULT 'Confirmed';

-- ── 7. FIX seats.status ENUM — add 'checked_in' ──────────────
ALTER TABLE seats
    MODIFY COLUMN status
        ENUM('available','locked','booked','checked_in')
        NOT NULL DEFAULT 'available';

-- ── 8. PERFORMANCE INDEXES ────────────────────────────────────
ALTER TABLE bookings
    ADD INDEX IF NOT EXISTS idx_booking_type  (booking_type),
    ADD INDEX IF NOT EXISTS idx_expiry_time   (expiry_time),
    ADD INDEX IF NOT EXISTS idx_checked_in_at (checked_in_at);

ALTER TABLE qr_verification_logs
    ADD INDEX IF NOT EXISTS idx_scanned_by_date (scanned_by, scanned_at);

-- ── 9. VERIFY ────────────────────────────────────────────────
SELECT 'Schema fix complete!' AS status;

SELECT
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='bookings'
       AND COLUMN_NAME='booking_type')  AS has_booking_type,
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='bookings'
       AND COLUMN_NAME='qr_code_data')  AS has_qr_code_data,
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='bookings'
       AND COLUMN_NAME='checked_in_at') AS has_checked_in_at,
    (SELECT COUNT(*) FROM information_schema.TABLES
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='staff_profiles') AS has_staff_profiles,
    (SELECT COUNT(*) FROM information_schema.TABLES
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='qr_verification_logs') AS has_qr_logs,
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='users'
       AND COLUMN_NAME='role') AS has_user_role;
