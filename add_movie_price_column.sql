-- ════════════════════════════════════════════════════════════════════════════════
-- MIGRATION: Add price column to movies table
-- Run this in MySQL Workbench before starting the Flask app
-- ════════════════════════════════════════════════════════════════════════════════

USE tickit_db;

-- Add price column to movies table if it doesn't exist
ALTER TABLE movies
ADD COLUMN IF NOT EXISTS price SMALLINT UNSIGNED NOT NULL DEFAULT 450;

-- Verify the column was added
SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='movies' AND COLUMN_NAME='price';

-- Show all columns in movies table
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='movies'
ORDER BY ORDINAL_POSITION;

SELECT '✓ Migration complete! Price column added to movies table.' AS status;
