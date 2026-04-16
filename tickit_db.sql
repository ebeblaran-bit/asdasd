-- ============================================================
--  TICK.IT — MySQL Schema (FIXED)
--  Run this in MySQL Workbench before starting the Flask app
-- ============================================================

CREATE DATABASE IF NOT EXISTS tickit_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE tickit_db;

-- ── USERS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    email      VARCHAR(255) UNIQUE,
    mobile     VARCHAR(20)  UNIQUE,
    full_name  VARCHAR(255) NOT NULL,
    age        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    gender     ENUM('Male','Female','Non-binary','Prefer not to say') NOT NULL DEFAULT 'Prefer not to say',
    address    VARCHAR(500) NOT NULL DEFAULT '',
    password   VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_contact CHECK (email IS NOT NULL OR mobile IS NOT NULL),
    INDEX idx_email (email),
    INDEX idx_mobile (mobile)
);

-- ── MOVIES ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS movies (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    genre         VARCHAR(100) NOT NULL,
    rating        DECIMAL(3,1) NOT NULL DEFAULT 0.0,
    poster_path   VARCHAR(500) NOT NULL DEFAULT 'images/no_poster.png',
    duration_mins SMALLINT UNSIGNED NOT NULL DEFAULT 120,
    price         SMALLINT UNSIGNED NOT NULL DEFAULT 450,
    description   TEXT,
    cast_members  VARCHAR(500),
    release_date  DATE,
    status        ENUM('active','inactive') NOT NULL DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_title (title)
);

-- ── CINEMAS ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cinemas (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    name     VARCHAR(255) NOT NULL,
    location VARCHAR(500) NOT NULL,
    screens  TINYINT UNSIGNED NOT NULL DEFAULT 1,
    INDEX idx_name (name)
);

-- ── CINEMA HALLS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cinema_halls (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    cinema_id   INT NOT NULL,
    hall_name   VARCHAR(100) NOT NULL,
    rows_count  TINYINT UNSIGNED NOT NULL DEFAULT 8,
    cols_count  TINYINT UNSIGNED NOT NULL DEFAULT 10,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE CASCADE,
    UNIQUE KEY uq_hall (cinema_id, hall_name),
    INDEX idx_cinema_id (cinema_id)
);

-- ── SHOWINGS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS showings (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    movie_id    INT NOT NULL,
    cinema_id   INT NOT NULL,
    hall_id     INT NULL,
    show_date   DATE NOT NULL,
    show_time   TIME NOT NULL,
    total_seats TINYINT UNSIGNED NOT NULL DEFAULT 50,
    status      ENUM('open','scheduled','full','completed') NOT NULL DEFAULT 'open',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (movie_id)  REFERENCES movies(id)  ON DELETE CASCADE,
    FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE CASCADE,
    FOREIGN KEY (hall_id)   REFERENCES cinema_halls(id) ON DELETE SET NULL,
    UNIQUE KEY uq_showing_hall (hall_id, show_date, show_time),
    INDEX idx_movie_id (movie_id),
    INDEX idx_cinema_id (cinema_id),
    INDEX idx_show_date (show_date),
    INDEX idx_status (status)
);

-- ── SEATS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seats (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    showing_id   INT NOT NULL,
    row_label    CHAR(1) NOT NULL,
    seat_number  TINYINT UNSIGNED NOT NULL,
    seat_code    VARCHAR(6) NOT NULL,
    category     ENUM('VIP','Standard') NOT NULL DEFAULT 'Standard',
    status       ENUM('available','locked','booked') NOT NULL DEFAULT 'available',
    locked_until DATETIME NULL,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE,
    UNIQUE KEY uq_seat (showing_id, seat_code),
    INDEX idx_showing_id (showing_id),
    INDEX idx_status (status),
    INDEX idx_locked_until (locked_until)
);

-- ── BOOKINGS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bookings (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    user_id              INT NOT NULL,
    showing_id           INT NOT NULL,
    seat_id              INT NOT NULL,
    booking_ref          VARCHAR(20),
    ref_code             VARCHAR(20) NOT NULL UNIQUE,
    ticket_type          ENUM('Regular','Student','Senior / PWD') NOT NULL DEFAULT 'Regular',
    ticket_count         TINYINT UNSIGNED NOT NULL DEFAULT 1,
    unit_price           DECIMAL(10,2) NOT NULL DEFAULT 450.00,
    total_price          DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    seat_codes           VARCHAR(500),
    customer_name        VARCHAR(255) NOT NULL,
    contact              VARCHAR(20)  NOT NULL,
    special_requests     TEXT,
    discount_status      ENUM('none','pending_verification','verified','rejected') NOT NULL DEFAULT 'none',
    payment_status       ENUM('pending','paid','failed','refunded','walkin_pending') NOT NULL DEFAULT 'pending',
    status               ENUM('Confirmed','Cancelled','Completed') NOT NULL DEFAULT 'Confirmed',
    verification_details TEXT NULL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (showing_id) REFERENCES showings(id) ON DELETE CASCADE,
    FOREIGN KEY (seat_id)    REFERENCES seats(id)    ON DELETE CASCADE,
    INDEX idx_ref_code (ref_code),
    INDEX idx_user_id (user_id),
    INDEX idx_showing_id (showing_id),
    INDEX idx_payment_status (payment_status),
    INDEX idx_discount_status (discount_status),
    INDEX idx_created_at (created_at)
);

-- ── PAYMENTS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    booking_ref       VARCHAR(20) NOT NULL,
    user_id           INT NULL,
    amount            DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    payment_method    VARCHAR(50) NOT NULL DEFAULT 'credit_card',
    paymongo_link_id  VARCHAR(100) NULL,
    status            ENUM('pending','paid','failed','refunded') NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at           DATETIME NULL,
    failed_at         DATETIME NULL,
    INDEX idx_booking_ref (booking_ref),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
);

-- ── PAYMONGO MOCK LINKS (for testing without real API keys) ──
CREATE TABLE IF NOT EXISTS paymongo_mock_links (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    link_id       VARCHAR(50) NOT NULL UNIQUE,
    ref_code      VARCHAR(20) NOT NULL,
    amount        DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    description   VARCHAR(255) NULL,
    status        ENUM('unpaid','paid','failed','expired') NOT NULL DEFAULT 'unpaid',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NULL,
    INDEX idx_link_id (link_id),
    INDEX idx_ref_code (ref_code),
    INDEX idx_status (status)
);

-- ── HALL SEAT CONFIG ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hall_seat_config (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    hall_id     INT NOT NULL,
    row_label   CHAR(1) NOT NULL,
    col_number  TINYINT UNSIGNED NOT NULL,
    seat_code   VARCHAR(6) NOT NULL,
    seat_type   ENUM('Regular','VIP','PWD') NOT NULL DEFAULT 'Regular',
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (hall_id) REFERENCES cinema_halls(id) ON DELETE CASCADE,
    UNIQUE KEY uq_hall_seat (hall_id, seat_code),
    INDEX idx_hall_id (hall_id)
);

-- ── SEED CINEMAS ─────────────────────────────────────────────
INSERT IGNORE INTO cinemas (id, name, location, screens) VALUES
    (1, 'SM Seaside Cebu',           'SRP, Cebu City',        6),
    (2, 'Gaisano Grand Minglanilla', 'Minglanilla, Cebu',     4),
    (3, 'Nustar Cebu Cinema',        'SRP, Cebu City',        5),
    (4, 'Cebu IL CORSO Cinema',      'South Road Properties', 4),
    (5, 'UC Cantao-an',              'Naga, Cebu',            2),
    (6, 'TOPS Cebu Skydom',          'Busay, Cebu City',      3);

-- Reset auto-increment if needed
ALTER TABLE cinemas AUTO_INCREMENT = 7;

-- ════════════════════════════════════════════════════════════════════════════════
-- FIX STRATEGY: Remove problematic constraints and recreate cleanly
-- ════════════════════════════════════════════════════════════════════════════════

-- Drop existing foreign key if present (safe operation)
ALTER TABLE showings 
    DROP FOREIGN KEY IF EXISTS fk_showing_hall;

-- Drop existing unique constraint if present
ALTER TABLE showings 
    DROP INDEX IF EXISTS uq_showing_hall;

-- Drop existing unique constraint if present (old format)
ALTER TABLE showings 
    DROP INDEX IF EXISTS uq_showing;

-- Now add the hall_id column if it doesn't exist
ALTER TABLE showings
    ADD COLUMN IF NOT EXISTS hall_id INT NULL AFTER cinema_id;

-- Add the new foreign key
ALTER TABLE showings
    ADD CONSTRAINT fk_showing_hall_v2 FOREIGN KEY (hall_id)
        REFERENCES cinema_halls(id) ON DELETE SET NULL;

-- Add the new unique constraint considering hall_id
ALTER TABLE showings
    ADD UNIQUE KEY IF NOT EXISTS uq_showing_hall (hall_id, show_date, show_time);

-- Add indexes to showings if not present
ALTER TABLE showings 
    ADD INDEX IF NOT EXISTS idx_movie_id (movie_id);
ALTER TABLE showings 
    ADD INDEX IF NOT EXISTS idx_cinema_id (cinema_id);
ALTER TABLE showings 
    ADD INDEX IF NOT EXISTS idx_show_date (show_date);
ALTER TABLE showings 
    ADD INDEX IF NOT EXISTS idx_status (status);

-- ════════════════════════════════════════════════════════════════════════════════
-- BOOKINGS TABLE ENHANCEMENTS
-- ════════════════════════════════════════════════════════════════════════════════

-- Add columns if not already present
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS discount_status
        ENUM('none','pending_verification','verified','rejected')
        NOT NULL DEFAULT 'none' AFTER special_requests;

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS payment_status
        ENUM('pending','paid','failed','refunded','walkin_pending')
        NOT NULL DEFAULT 'pending' AFTER discount_status;

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS verification_details
        TEXT NULL AFTER payment_status;

-- Fix unit_price: change from SMALLINT to DECIMAL if needed
ALTER TABLE bookings
    MODIFY COLUMN unit_price DECIMAL(10,2) NOT NULL DEFAULT 450.00;

-- Add ref_code as UNIQUE if not already unique
ALTER TABLE bookings
    ADD UNIQUE KEY IF NOT EXISTS uq_ref_code (ref_code);

-- Add performance indexes
ALTER TABLE bookings
    ADD INDEX IF NOT EXISTS idx_showing_id (showing_id);
ALTER TABLE bookings
    ADD INDEX IF NOT EXISTS idx_payment_status (payment_status);
ALTER TABLE bookings
    ADD INDEX IF NOT EXISTS idx_discount_status (discount_status);
ALTER TABLE bookings
    ADD INDEX IF NOT EXISTS idx_created_at (created_at);

-- ════════════════════════════════════════════════════════════════════════════════
-- SEATS TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE seats
    ADD INDEX IF NOT EXISTS idx_showing_id (showing_id);
ALTER TABLE seats
    ADD INDEX IF NOT EXISTS idx_status (status);
ALTER TABLE seats
    ADD INDEX IF NOT EXISTS idx_locked_until (locked_until);

-- ════════════════════════════════════════════════════════════════════════════════
-- PAYMENTS TABLE ENHANCEMENTS
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE payments
    ADD INDEX IF NOT EXISTS idx_status (status);
ALTER TABLE payments
    ADD INDEX IF NOT EXISTS idx_created_at (created_at);

-- ════════════════════════════════════════════════════════════════════════════════
-- USERS TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE users
    ADD INDEX IF NOT EXISTS idx_email (email);
ALTER TABLE users
    ADD INDEX IF NOT EXISTS idx_mobile (mobile);

-- ════════════════════════════════════════════════════════════════════════════════
-- MOVIES TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE movies
    ADD INDEX IF NOT EXISTS idx_status (status);
ALTER TABLE movies
    ADD INDEX IF NOT EXISTS idx_title (title);

-- ════════════════════════════════════════════════════════════════════════════════
-- CINEMA HALLS TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE cinema_halls
    ADD INDEX IF NOT EXISTS idx_cinema_id (cinema_id);

-- ════════════════════════════════════════════════════════════════════════════════
-- CINEMAS TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE cinemas
    ADD INDEX IF NOT EXISTS idx_name (name);

-- ════════════════════════════════════════════════════════════════════════════════
-- HALL SEAT CONFIG TABLE PERFORMANCE INDEXES
-- ════════════════════════════════════════════════════════════════════════════════

ALTER TABLE hall_seat_config
    ADD INDEX IF NOT EXISTS idx_hall_id (hall_id);

-- ════════════════════════════════════════════════════════════════════════════════
-- SCHEMA VERIFICATION
-- ════════════════════════════════════════════════════════════════════════════════

SELECT 'Schema verification:' AS status;
SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='users')
        THEN '✓ users table exists'
        ELSE '✗ users table missing'
    END AS check_1;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='movies')
        THEN '✓ movies table exists'
        ELSE '✗ movies table missing'
    END AS check_2;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='cinemas')
        THEN '✓ cinemas table exists'
        ELSE '✗ cinemas table missing'
    END AS check_3;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='showings')
        THEN '✓ showings table exists'
        ELSE '✗ showings table missing'
    END AS check_4;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='seats')
        THEN '✓ seats table exists'
        ELSE '✗ seats table missing'
    END AS check_5;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='bookings' AND COLUMN_NAME='payment_status')
        THEN '✓ bookings.payment_status exists'
        ELSE '✗ bookings.payment_status missing'
    END AS check_6;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='bookings' AND COLUMN_NAME='discount_status')
        THEN '✓ bookings.discount_status exists'
        ELSE '✗ bookings.discount_status missing'
    END AS check_7;

SELECT 
    CASE 
        WHEN EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='showings' AND COLUMN_NAME='hall_id')
        THEN '✓ showings.hall_id exists'
        ELSE '✗ showings.hall_id missing'
    END AS check_8;

-- Schema setup complete
SELECT 'Database setup complete!' AS final_message;