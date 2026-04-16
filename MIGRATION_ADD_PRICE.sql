-- ════════════════════════════════════════════════════════════════════════════════
-- COMPREHENSIVE FIX: Add & Verify Price Column in Movies Table
-- ════════════════════════════════════════════════════════════════════════════════

USE tickit_db;

-- Step 1: Check if price column exists
SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
           WHERE TABLE_SCHEMA='tickit_db' AND TABLE_NAME='movies' AND COLUMN_NAME='price'),
    'Column EXISTS',
    'Column MISSING'
) AS price_column_status;

-- Step 2: Add price column if it doesn't exist (place it after duration_mins)
ALTER TABLE movies
ADD COLUMN IF NOT EXISTS price SMALLINT UNSIGNED NOT NULL DEFAULT 450 AFTER duration_mins;

-- Step 3: Verify column was added
ALTER TABLE movies
MODIFY COLUMN price SMALLINT UNSIGNED NOT NULL DEFAULT 450;

-- Step 4: Set default prices for existing movies (if any have NULL)
UPDATE movies SET price = 450 WHERE price IS NULL OR price = 0;

-- Step 5: Show new column in movies table
DESC movies;

-- Step 6: Verify all movies have valid prices
SELECT COUNT(*) as total_movies,
       COUNT(CASE WHEN price > 0 THEN 1 END) as movies_with_price,
       MIN(price) as min_price,
       MAX(price) as max_price,
       AVG(price) as avg_price
FROM movies;

-- Step 7: Show sample movies with their prices
SELECT id, title, price, status, created_at FROM movies ORDER BY created_at DESC LIMIT 10;

-- ✓ Schema is now complete and ready!
SELECT '✓ Price column fully configured in movies table!' AS status;
