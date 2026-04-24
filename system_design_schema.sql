-- ============================================================
--  TICK.IT SYSTEM DESIGN - Schema Updates
--  Movie Theater Booking System with QR & Staff Verification
-- ============================================================

-- Select the database first
USE tickit_db;

-- ── 1. ADD USER ROLES ─────────────────────────────────────────
-- Check if column exists first, then add it
SET @dbname = DATABASE();
SET @tablename = "users";
SET @columnname = "role";

SET @preparedStatement = (SELECT IF(
    (
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = @tablename
        AND TABLE_SCHEMA = @dbname
        AND COLUMN_NAME = @columnname
    ) > 0,
    "SELECT 'Column role already exists in users table' AS message;",
    "ALTER TABLE users ADD COLUMN role ENUM('customer','staff','admin') NOT NULL DEFAULT 'customer' AFTER password;"
));

PREPARE addColumnIfNotExists FROM @preparedStatement;
EXECUTE addColumnIfNotExists;
DEALLOCATE PREPARE addColumnIfNotExists;

-- Update existing admin
UPDATE users SET role='admin' WHERE email='admin@gmail.com';

-- ── 2. STAFF PROFILE TABLE ────────────────────────────────────
CREATE TABLE IF NOT EXISTS staff_profiles (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL UNIQUE,
    employee_id     VARCHAR(20) NOT NULL UNIQUE,
    cinema_id       INT NULL,
    shift_start     TIME NULL,
    shift_end       TIME NULL,
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL,
    INDEX idx_employee_id (employee_id),
    INDEX idx_cinema_id (cinema_id)
);

-- ── 3. EXTEND BOOKINGS FOR QR & STATUS WORKFLOW ───────────────
-- Add new columns safely (check if exists first)

-- qr_code_data
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='qr_code_data') = 0,
    'ALTER TABLE bookings ADD COLUMN qr_code_data TEXT NULL AFTER ref_code',
    'SELECT "qr_code_data already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- qr_image_path
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='qr_image_path') = 0,
    'ALTER TABLE bookings ADD COLUMN qr_image_path VARCHAR(500) NULL AFTER qr_code_data',
    'SELECT "qr_image_path already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- booking_type
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='booking_type') = 0,
    "ALTER TABLE bookings ADD COLUMN booking_type ENUM('online','walkin') NOT NULL DEFAULT 'online' AFTER status",
    'SELECT "booking_type already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- expiry_time
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='expiry_time') = 0,
    'ALTER TABLE bookings ADD COLUMN expiry_time DATETIME NULL AFTER created_at',
    'SELECT "expiry_time already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- checked_in_at
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='checked_in_at') = 0,
    'ALTER TABLE bookings ADD COLUMN checked_in_at DATETIME NULL',
    'SELECT "checked_in_at already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- checked_in_by
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='checked_in_by') = 0,
    'ALTER TABLE bookings ADD COLUMN checked_in_by INT NULL',
    'SELECT "checked_in_by already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- checked_in_cinema_id
SET @sql = (SELECT IF(
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' AND COLUMN_NAME='checked_in_cinema_id') = 0,
    'ALTER TABLE bookings ADD COLUMN checked_in_cinema_id INT NULL',
    'SELECT "checked_in_cinema_id already exists" AS message'
)); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Modify status enum to include pending/expired/checked_in (run only if needed)
-- Note: This may fail if existing data has 'Confirmed' - update data first if needed
-- First, update any existing 'Confirmed' to 'confirmed' (lowercase)
UPDATE bookings SET status='confirmed' WHERE status='Confirmed';

-- Then modify the enum
ALTER TABLE bookings 
    MODIFY COLUMN status ENUM('pending','confirmed','expired','cancelled','completed','checked_in') 
    NOT NULL DEFAULT 'pending';

-- Add foreign keys for check-in tracking (ignore errors if already exist)
-- Check and add foreign key for checked_in_by
SET @fk_name = (SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS 
                WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' 
                AND CONSTRAINT_NAME LIKE '%checked_in_by%');
SET @sql = IF(@fk_name IS NULL, 
    'ALTER TABLE bookings ADD FOREIGN KEY (checked_in_by) REFERENCES users(id) ON DELETE SET NULL',
    'SELECT "FK checked_in_by already exists" AS message'
); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Check and add foreign key for checked_in_cinema_id
SET @fk_name = (SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS 
                WHERE TABLE_SCHEMA=@dbname AND TABLE_NAME='bookings' 
                AND CONSTRAINT_NAME LIKE '%checked_in_cinema_id%');
SET @sql = IF(@fk_name IS NULL, 
    'ALTER TABLE bookings ADD FOREIGN KEY (checked_in_cinema_id) REFERENCES cinemas(id) ON DELETE SET NULL',
    'SELECT "FK checked_in_cinema_id already exists" AS message'
); PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 4. QR CODE VERIFICATION LOG ───────────────────────────────
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

-- ── 5. BOOKING STATUS HISTORY ─────────────────────────────────
CREATE TABLE IF NOT EXISTS booking_status_history (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    booking_id      INT NOT NULL,
    old_status      VARCHAR(20) NULL,
    new_status      VARCHAR(20) NOT NULL,
    changed_by      INT NULL,
    changed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason          TEXT NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_booking_id (booking_id)
);

-- ── 6. UPDATE SEAT STATUS ENUM ───────────────────────────────
-- Update existing data first to match new enum values
UPDATE seats SET status='available' WHERE status NOT IN ('available','locked','booked');

-- Then modify the enum
ALTER TABLE seats 
    MODIFY COLUMN status ENUM('available','locked','booked','checked_in') 
    NOT NULL DEFAULT 'available';
