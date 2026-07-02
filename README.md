# Corafone Voice Gateway

The Corafone automated voice engine, built up in phases. Phase 1 laid the data foundation (Supabase schema + an offline multi-agent simulation sandbox). Phase 2 adds the live, real-time core of the gateway.

## Project Structure

* `app/main.py` - The live web server core. Exposes a JSON health check endpoint and a persistent binary WebSocket pipeline (`/ws/stream`) that streams incoming audio data directly into Deepgram's cloud neural networks.
* `app/simulation.py` - The offline sandbox script. Runs an automated turn-by-turn conversation between an AI Collector persona and an adversarial AI Consumer persona, passes the transcript to a GPT-4o auditing judge, and logs the structured metrics straight to Supabase.
* `app/database/script.sql` - Schema definitions and seed data for the Supabase tables (accounts, communication logs, session metrics, and AI evaluation logs).
* `test_stream.py` - A localized client automation script used to simulate live network traffic. It bypasses complex telephony integrations by generating valid, alternating PCM carrier wave blocks and streaming them over the WebSocket pipeline to test system stability.
* `.env` - Local environment configuration file holding API credentials and connection strings (excluded from Git).

## Local Setup & Installation

Make sure your virtual environment is active before installing the dependencies.

```bash
# Create and activate the virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the required packages
pip install fastapi uvicorn websockets asyncpg openai pydantic python-dotenv deepgram-sdk
```

Configure your `.env` file in the root directory:

```text
OPENAI_API_KEY=your_openai_api_key_here
DATABASE_URL=your_supabase_session_pooler_connection_string
DEEPGRAM_API_KEY=your_real_deepgram_key_here
```

Note: Make sure to use the Supabase Connection Pooler URI string (port 6543) to bypass local DNS resolution bottlenecks.

---

## Phase 1: Data Engine & Simulation Sandbox

Phase 1 focuses on setting up our persistent cloud database layer on Supabase, establishing our target relational schemas, and running an autonomous multi-agent simulation sandbox to test agent negotiations and compliance auditing before wiring up live audio streams.

### Database Initialization

Run the contents of [`app/database/script.sql`](app/database/script.sql) inside your Supabase SQL Editor to spin up the target tables and seed the initial mock account.

```sql
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
```

### Running the Multi-Agent Sandbox

The simulation script uses two specialized LLM instances to simulate an outbound collection call.

* **Cora (AI Collector):** Operates under rigid corporate boundaries. She must state the legal Mini-Miranda disclosure on turn one and cannot offer more than a 40% settlement discount.
* **John (AI Consumer):** Acts as an adversarial actor experiencing financial hardship, demanding a 60% discount and pushing boundaries.

Once the dialogue loop terminates, a separate structured audit engine (GPT-4o) acts as a regulatory judge to verify legal compliance, rate the agent's tone, and write the resulting session records to Supabase.

To run the simulation sandbox:

```bash
python app/simulation.py
```

The console will stream the live negotiation followed by confirmation of the session and evaluation records being committed to the database.

---

## Phase 2: Real-Time Streaming Gateway

Now that the database foundation is locked down, Phase 2 focuses on building the real-time core of the gateway. Transitioned from static text simulations to a live streaming server using **FastAPI WebSockets** and integrated **Deepgram's Asynchronous Streaming SDK** to handle ultra-low-latency real-time voice transcription.

### Booting the Gateway Server

To boot up the live gateway backend, execute the following command:

```bash
uvicorn app.main:app --reload
```

#### Verifying Gateway Health

Open a separate terminal window and verify the HTTP service layer responds with a clean `200 OK` status code:

```bash
curl http://127.0.0.1:8000/health
```

**Expected Response:**

```json
{"status":"healthy","gateway":"operational"}
```

### Simulating Real-Time Binary Media Streams

While keeping the Uvicorn server active in terminal one, spin up the client automation test script in a separate terminal:

```bash
python test_stream.py
```

#### Observed Execution Lifecycle

The server smoothly captures the client attachment, opens up a secure connection upstream to Deepgram, transfers the media data block-by-block, and signs off with a standard closure sequence:

```text
INFO:     127.0.0.1:58669 - "WebSocket /ws/stream" [accepted]
Telephony or web client connected to streaming socket gateway.
INFO:     connection open
Bidirectional Deepgram audio pipeline successfully initialized.
...
Telephony client closed connection normally (1000 OK). Clean teardown executed.
INFO:     connection closed
```
