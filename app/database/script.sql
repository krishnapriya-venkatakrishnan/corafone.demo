-- 1. Core account tracking
CREATE TABLE IF NOT EXISTS accounts (
    account_id SERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    current_balance NUMERIC(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SETTLED', 'DO_NOT_CALL', 'DISPUTE'))
);

-- 2. Inter-channel interaction log
CREATE TABLE IF NOT EXISTS communication_logs (
    log_id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    channel VARCHAR(10) CHECK (channel IN ('VOICE', 'SMS', 'EMAIL')),
    direction VARCHAR(10) CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Session metric collector
CREATE TABLE IF NOT EXISTS voice_session_metrics (
    session_id VARCHAR(50) PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    total_duration_seconds INT NOT NULL,
    avg_latency_ms INT NOT NULL,
    barge_in_count INT DEFAULT 0,
    disposition_code VARCHAR(30) NOT NULL
);

-- 4. Automated Compliance Logs
CREATE TABLE IF NOT EXISTS ai_evaluation_logs (
    eval_id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) REFERENCES voice_session_metrics(session_id) ON DELETE CASCADE,
    mini_miranda_passed BOOLEAN NOT NULL,
    pii_redacted_correctly BOOLEAN NOT NULL,
    hallucination_detected BOOLEAN NOT NULL,
    tone_score INT CHECK (tone_score BETWEEN 1 AND 5),
    judge_reasoning TEXT NOT NULL,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Seed a test user immediately
INSERT INTO accounts (customer_name, phone_number, current_balance, status)
VALUES ('John', '+15550199', 500.00, 'ACTIVE')
ON CONFLICT (phone_number) DO NOTHING;