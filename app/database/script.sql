-- 1. Core account tracking
CREATE TABLE IF NOT EXISTS accounts (
    account_id SERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    current_balance NUMERIC(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SETTLED', 'PAYMENT_PLAN_ACTIVE', 'DO_NOT_CALL', 'DISPUTE')),
    requires_manual_review BOOLEAN NOT NULL DEFAULT FALSE
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
        CHECK (disposition_code IN (
            'SETTLED', 'PAYMENT_PLAN_ACTIVE', 'NO_ACTION', 'ESCALATED_NO_AGREEMENT'
        )),
    error_count INT NOT NULL DEFAULT 0,
    transcript_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Automated Compliance Logs
CREATE TABLE IF NOT EXISTS ai_evaluation_logs (
    eval_id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) REFERENCES voice_session_metrics(session_id) ON DELETE CASCADE,
    mini_miranda_passed BOOLEAN NOT NULL,
    pii_redacted_correctly BOOLEAN NOT NULL,
    hallucination_detected BOOLEAN NOT NULL,
    identity_verified_before_disclosure BOOLEAN NOT NULL,
    prohibited_conduct_detected BOOLEAN NOT NULL,
    right_to_cease_honored BOOLEAN,
    tone_score INT CHECK (tone_score BETWEEN 1 AND 5),
    judge_reasoning TEXT NOT NULL,
    judge_cost_usd NUMERIC(10, 6),
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Payment plans -- every negotiated agreement lands here (see
-- app/tools.py's _persist_agreement), single-payment settlements included,
-- so a deferred lump sum still has its date recorded somewhere queryable;
-- a single payment additionally sets accounts.status = 'SETTLED'.
-- app/db.py's reset_demo_account marks a demo account's rows 'SUPERSEDED'
-- (never deletes them) so agreement history survives across calls while
-- get_active_payment_plans's status = 'ACTIVE' filter keeps a superseded
-- row from reading as a live commitment.
CREATE TABLE IF NOT EXISTS payment_plans (
    plan_id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    num_installments INT NOT NULL,
    amount_per_installment NUMERIC(10, 2) NOT NULL,
    total_amount NUMERIC(10, 2) NOT NULL,
    start_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
    payments_breakdown TEXT,
    -- How many times each concession gate fired before this agreement was
    -- reached -- distinguishes accepting the opening offer (0/0) from
    -- holding out on a discount or a first-payment date.
    discount_counters_issued INT NOT NULL DEFAULT 0,
    date_counters_issued INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Scheduled callbacks -- read-only from the live agent (it has no
-- callback-scheduling tool); rows come from seed data or a prior human
-- agent, never written by Cora. See README's "scope" section.
CREATE TABLE IF NOT EXISTS scheduled_callbacks (
    callback_id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    callback_time TIMESTAMP NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Seed the graded demo account. ON CONFLICT DO UPDATE, not DO NOTHING --
-- the task scenario is a $1,000, 180+ day delinquent balance; every ladder
-- number in the README ($800 ceiling, $250 floor, $750/$250) only lines up
-- at $1,000, and DO NOTHING would silently leave a stale row (e.g. an
-- earlier $500 seed) uncorrected on re-run. app/db.py's
-- reset_demo_account additionally restores this exact state before every
-- call, so a prior call's settlement/plan never carries into the next one.
INSERT INTO accounts (customer_name, phone_number, current_balance, status, requires_manual_review)
VALUES ('John Callahan', '+15550199', 1000.00, 'ACTIVE', FALSE)
ON CONFLICT (phone_number) DO UPDATE SET
    customer_name = EXCLUDED.customer_name,
    current_balance = EXCLUDED.current_balance,
    status = EXCLUDED.status,
    requires_manual_review = EXCLUDED.requires_manual_review;
