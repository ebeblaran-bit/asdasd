-- ════════════════════════════════════════════════════════════════════════════════
-- CREATE PAYMONGO MOCK LINKS TABLE (for testing without real PayMongo API keys)
-- ════════════════════════════════════════════════════════════════════════════════

USE tickit_db;

-- Create table for mock PayMongo links (used when real API keys not configured)
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

-- Verify table was created
SELECT '✅ paymongo_mock_links table created successfully!' AS status;
DESC paymongo_mock_links;

-- ════════════════════════════════════════════════════════════════════════════════
-- TESTING: Test the mock payment flow
-- ════════════════════════════════════════════════════════════════════════════════

-- Insert a test mock link
INSERT INTO paymongo_mock_links (link_id, ref_code, amount, description, status)
VALUES ('MOCK-TEST123456789ABCDEF', 'TEST-100', 50000, 'Test payment for Avatar', 'unpaid');

-- Verify it's there
SELECT * FROM paymongo_mock_links WHERE link_id = 'MOCK-TEST123456789ABCDEF';

-- Clean up test data
DELETE FROM paymongo_mock_links WHERE link_id LIKE 'MOCK-TEST%';

SELECT '✅ Mock PayMongo testing complete!' AS status;
