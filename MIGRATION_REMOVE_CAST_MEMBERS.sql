-- Migration: Remove cast_members field (now included in description)
-- This migration makes cast_members nullable since it's no longer used in the UI
-- The description field now contains all movie information including cast

-- Make cast_members nullable (optional)
ALTER TABLE movies MODIFY COLUMN cast_members VARCHAR(500) NULL;

-- Optional: You can also drop the column entirely if you want to clean up the database
-- Uncomment the line below if you want to permanently remove the column:
-- ALTER TABLE movies DROP COLUMN cast_members;

-- Note: The admin interface now only uses the description field for all movie details
-- including cast information, plot summary, and other details.
