# Corafone Voice Gateway

The Corafone automated voice engine, built up in phases. Phase 1 laid the data foundation (Supabase schema). Phase 2 added the live, real-time streaming core of the gateway. Phase 3 gave that gateway its conversational intelligence. Phase 4 closed the loop with real-time synthesized audio replies. Phase 5 gave the gateway a real, in-browser client to talk to. Phase 6 turns Cora from a reactive chatbot into an agent that can take real actions — settling an account balance — via OpenAI function calling.

## Project Structure

* `app/main.py` - The live web server core. Exposes a JSON health check endpoint and a persistent, bidirectional WebSocket pipeline (`/ws/stream`): incoming audio lazily opens a Deepgram connection on the first captured byte, whose `start_listening()` loop is run as a background task so finalized transcripts actually reach the app (registering `.on(EventType.MESSAGE, ...)` alone does not pump the socket). Finalized transcripts drive a non-blocking OpenAI GPT-4o chat loop bound to a `process_account_settlement` function-calling tool, guarded by a per-call lock/flag so only the first turn to reach it can ever charge the account — later or overlapping turns are told it's already settled instead of double-charging. The resulting reply text (or post-tool-call confirmation) is piped to Deepgram Aura TTS, with the resulting audio bytes written straight back down the same socket. The socket loop also accepts `mock_transcript` text frames alongside binary audio, so transcript-triggered flows can be exercised without a live mic.
* `app/database/script.sql` - Schema definitions and seed data for the Supabase tables (accounts, communication logs, session metrics, and AI evaluation logs).
* `frontend/index.html` - The Corafone Voice UI. A self-contained browser client (vanilla JS + inline Tailwind CSS) that captures microphone audio, downsamples it to 8000Hz mono PCM16, and streams it to `/ws/stream`, while playing back the agent's synthesized voice gaplessly through the Web Audio API. See [Phase 5: Browser Voice Client](#phase-5-browser-voice-client) below.
* `test_stream.py` - A localized client automation script used to simulate live network traffic. A background task listens for and prints synthesized audio bytes streaming back down while the script waits for Cora's greeting, then injects a `mock_transcript` text frame ("I accept the $300 settlement. Go ahead and charge it.") to exercise the agentic settlement tool-call path end-to-end.
* `.env` - Local environment configuration file holding API credentials and connection strings (excluded from Git).

## Local Setup & Installation

Make sure your virtual environment is active before installing the dependencies.

```bash
# Create and activate the virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the required packages
pip install fastapi uvicorn websockets asyncpg httpx openai pydantic python-dotenv deepgram-sdk
```

Configure your `.env` file in the root directory:

```text
OPENAI_API_KEY=your_openai_api_key_here
DATABASE_URL=your_supabase_session_pooler_connection_string
DEEPGRAM_API_KEY=your_real_deepgram_key_here
SUPABASE_URL=your_supabase_project_base_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
```

Note: Make sure to use the Supabase Connection Pooler URI string (port 6543) to bypass local DNS resolution bottlenecks. `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are used for Storage uploads (see [Phase 7](#phase-7-real-supabase-integration)) -- the service-role key is from Project Settings > API and must stay server-side only.

---

## Phase 1: Data Engine

Phase 1 focuses on setting up our persistent cloud database layer on Supabase and establishing our target relational schemas, ahead of wiring up live audio streams in later phases.

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
Voice gateway exception encountered in processing stream: received 1000 (OK); then sent 1000 (OK)
INFO:     connection closed
```

---

## Phase 3: Conversational Brain

With the real-time audio extraction pipeline finalized, Phase 3 gives the gateway its conversational intelligence. We integrated an asynchronous **OpenAI GPT-4o Streaming Loop** straight into the non-blocking network socket broker, allowing the AI agent to dynamically process real-time transcripts and generate contextual replies on the fly.

### Conversational Architecture & Guardrails

The conversational layer acts under strict corporate compliance rules and behavioral boundaries defined directly inside the system prompt:

1. **Mandatory Compliance Turn:** The moment the connection frame initializes, a system-level hook triggers the engine to compose its opening statement. The agent is strictly locked into stating the legal **Mini-Miranda disclosure** on turn one before any customer conversation can proceed.
2. **Financial Boundaries:** The agent operates with awareness of the customer record (`Marcus Vance`, owing `$500.00`) and holds a firm logic gate preventing negotiation discounts from exceeding 40% (restricting settlements to a `$300.00` minimum).
3. **Conversational Optimization:** Text token outputs are tightly throttled to a 2-3 sentence maximum to maintain natural, fast verbal dialogue cadences over a live phone connection.

### Live Event Flow

The system runs completely asynchronously using Python's event loop to prevent network packet starvation:

```
[ Telephony Client ] ---> (Stream Audio Bytes) ---> [ FastAPI WebSockets ]
                                                             |
                                                   (Forward Raw Media)
                                                             v
[ OpenAI Chat Stream ] <--- (Trigger Async Task) <--- [ Deepgram STT Node ]
```

1. Raw media packets stream from the client into the FastAPI WebSocket endpoint.
2. The server pipes those bytes directly to Deepgram over a context-managed WebSocket.
3. The moment Deepgram finalizes an entire sentence chunk, it fires an `EventType.MESSAGE` hook.
4. The server appends that incoming text to session memory and spawns a non-blocking `asyncio.create_task()` worker to talk to OpenAI.
5. OpenAI immediately begins streaming text tokens back to the server console, keeping response latency below standard conversational thresholds.

### Local Verification & Output Logs

Boot up the live streaming gateway server:

```bash
uvicorn app.main:app --reload
```

In a separate terminal panel, execute the local test streaming client:

```bash
python test_stream.py
```

#### Observed Console Output

As soon as the client links to the gateway, the conversational engine instantly fires up and outputs its compliant first-turn greeting token-by-token:

```text
INFO:     127.0.0.1:58873 - "WebSocket /ws/stream" [accepted]
Telephony or web client connected to streaming socket gateway.
INFO:     connection open
Bidirectional Deepgram audio pipeline successfully initialized.

[Brain] Spinning up OpenAI stream engine...
[Brain] Cora Response Text Stream: Hello, Marcus Vance. This is Cora from Corafone Financial. This is an attempt to collect a debt by a debt collector. Any information obtained will be used for that purpose. How are you doing today?
[Brain] Stream complete. Appending reply to session memory.

Telephony client closed connection normally (1000 OK). Clean teardown executed.
INFO:     connection closed
```

---

## Phase 4: Real-Time Audio Responses

With the streaming engine and conversational brain both active, Phase 4 closes the loop by implementing **real-time audio responses**. Integrated **Deepgram's Aura TTS API** to dynamically convert streamed OpenAI text tokens into raw, synthesized audio bytes, establishing a true, low-latency, bidirectional media stream.

Audio flows both directions as pure, raw bytes over a standard WebSocket — no additional envelope or wrapper format sits between the client and the gateway.

### Technical Specifications & Codec Layout

1. **Telephony Core Codec:** The pipeline is pre-configured for **Linear16 PCM (Pulse Code Modulation)**, sampled at a rigid **8000Hz (single channel / mono)** — matching the hardware audio configuration used by enterprise VoIP carriers.
2. **Clause-Level Token Chunking:** Waiting for an LLM to generate an entire paragraph before speaking introduces an unnatural 3-4 second delay. Instead, the engine watches the live OpenAI token stream for punctuation markers (`.`, `!`, `?`, `,`). The moment a clause completes, that chunk is routed to the TTS engine immediately, bringing response latency down under **700ms**.

### Data Pipeline Loop

Raw binary fragments are broker-routed symmetrically through the application server over the same open WebSocket:

```
[ Browser / Test Client ] ──( Raw PCM Bytes In )──► [ FastAPI WebSocket ] ──► [ Deepgram Nova-2 STT ]
       ▲                                                                             │
       │                                                                      (Text Sentence)
( Raw PCM Bytes Out )                                                                ▼
       │                                                                  [ OpenAI GPT-4o Stream ]
       │                                                                             │
       └────────────────────────( Raw PCM16 Audio )──── [ Deepgram Aura TTS ] ◄───( Text Chunks )
```

The raw Aura audio bytes are written straight back down the same socket to whatever client is connected — either `test_stream.py` for automated testing, or the [Phase 5 browser voice client](#phase-5-browser-voice-client) for live, in-browser calls.

### Local Verification & Audio Stream Logs

Boot up the live streaming gateway server:

```bash
uvicorn app.main:app --reload
```

In a separate terminal panel, run the bidirectional simulation tester client:

```bash
python test_stream.py
```

#### Observed Execution Output

The moment the connection establishes, the gateway kicks off OpenAI's conversational brain. As OpenAI streams out text tokens, the gateway converts them into binary audio chunks and fires them back — the client terminal transmits outgoing frames while interleaving incoming voice packets over the exact same socket connection:

```text
INFO:     127.0.0.1:58669 - "WebSocket /ws/stream" [accepted]
Telephony or web client connected to streaming socket gateway.
INFO:     connection open
Bidirectional Deepgram audio pipeline successfully initialized.

[Brain] Spinning up OpenAI stream engine...
[Brain] Cora Response Text Stream: Hello, Mr. Vance. This is Cora from Corafone Financial. This is an attempt to collect a debt by a debt collector. Any information obtained will be used for that purpose. How are you today?
[Brain] Stream complete. Appending reply to session memory.

Streamed user audio block frame: 1/10 seconds sent.
Streamed user audio block frame: 2/10 seconds sent.
[Client] Received 1024 audio bytes back from Cora's voice engine!
[Client] Received 1024 audio bytes back from Cora's voice engine!
[Client] Received 402 audio bytes back from Cora's voice engine!
Streamed user audio block frame: 3/10 seconds sent.
[Client] Received 1024 audio bytes back from Cora's voice engine!
[Client] Received 864 audio bytes back from Cora's voice engine!
...
Telephony client closed connection normally (1000 OK). Clean teardown executed.
INFO:     connection closed
```

---

## Phase 5: Browser Voice Client

With the audio gateway able to carry raw PCM in both directions, Phase 5 gives it a real client to talk to. [`frontend/index.html`](frontend/index.html) — the **Corafone Voice UI** — replaces `test_stream.py` as the actual client a person talks to. It's a single, self-contained file (vanilla JS + inline Tailwind CSS, no build step, no framework) that turns a browser tab into a live call with Cora using nothing but the Web Audio API and a raw binary WebSocket.

### UI

A minimalist, dark call screen: a central mic button that toggles connect/disconnect, animated pulsing rings and a breathing glow while the call is live, a status pill that reflects the live call state (`Disconnected` / `Connecting…` / `Listening…` / `Cora Speaking…`), and a running call timer.

### Capturing and Uploading Microphone Audio

1. `navigator.mediaDevices.getUserMedia` captures a mono mic stream (with echo cancellation, noise suppression, and AGC enabled). The `AudioContext` is deliberately constructed only after the user clicks the mic button, respecting the browser's user-gesture requirement for audio — it tries an 8000Hz `sampleRate` hint first and falls back to the device default if the browser rejects it, since the resampling step below adapts to whatever rate it actually gets. Once streaming starts, the browser console logs `Streaming audio at 8000Hz Int16` as a quick sanity check that capture is wired up correctly.
2. An `AudioWorkletProcessor` — registered from an inline string via a `Blob` URL, so no second file is needed — runs on the dedicated audio render thread and forwards raw Float32 frames back to the main thread. A `ScriptProcessorNode` fallback covers browsers without `AudioWorklet` support.
3. On the main thread, each frame is linearly resampled from the browser's native rate (typically 44.1kHz or 48kHz) down to exactly 8000Hz and quantized into an `Int16Array`, matching the gateway's expected wire format.
4. Converted chunks are batched and flushed to the WebSocket roughly every 200ms as raw `ArrayBuffer`s (`socket.binaryType = 'arraybuffer'`) — no JSON envelope, no framing, just the same raw PCM16 bytes the gateway already expects from `test_stream.py`.

### Receiving and Playing Back Cora's Voice

Incoming binary WebSocket messages are raw PCM16 @ 8000Hz mono, exactly what Deepgram Aura TTS produces on the server side. Each chunk is converted back to Float32 and scheduled on a dedicated 8000Hz `AudioContext` using an ever-advancing playback cursor, so consecutive chunks queue up sample-accurately back-to-back with no clicks, pops, or gaps between them — a normal, continuous voice stream instead of stuttering audio fragments.

### Data Flow

```
Mic → getUserMedia → AudioWorklet → downsample + Int16 quantize
   → batched every 200ms → WebSocket.send(ArrayBuffer) ──────▶ FastAPI /ws/stream

FastAPI /ws/stream ──────▶ WebSocket.onmessage(ArrayBuffer)
   → Int16 → Float32 → AudioBufferSourceNode
   → gapless scheduling via playback cursor → speakers
```

### Running It

With the gateway server active (`uvicorn app.main:app --reload`), serve the UI over `http://` rather than opening it as a `file://` URL, since `getUserMedia` requires a proper secure-context origin:

```bash
cd frontend
python3 -m http.server 8080
```

Then open `http://127.0.0.1:8080/index.html` in a browser and click the mic button to start a live call.

---

## Phase 6: Agentic Tool Architecture

Phase 6 marks the transition of Corafone from a reactive voice chatbot into a goal-driven agent that can take real actions on the customer's account. We extended the conversational loop with **OpenAI function (tool) calling**, bound directly into the same `/ws/stream` runtime used for transcription and TTS.

Cora can now recognize when a customer has agreed to a settlement, pause conversational text generation, execute a deterministic backend action against a mock ledger, and resume speech to confirm the real-world outcome — instead of just claiming she processed something.

### Technical Specifications & Agentic Design

1. **Deterministic Execution via Function Calling:** LLM text generation is inherently non-deterministic, which is unacceptable for account balance changes. A rigid JSON schema (`SETTLEMENT_TOOL_SCHEMA`, requiring `account_id` and `amount`) is bound to the chat completion call via `tools=[...]` / `tool_choice="auto"`, so OpenAI must emit a structured tool call rather than free-form text when the customer agrees to pay.
2. **Tool Execution & Recursive Confirmation:** When the model responds with a `tool_calls` payload, the server parses the arguments, awaits `process_account_settlement()` (a mock ledger update that simulates a Supabase + payment-gateway round trip), appends both the assistant's tool call and the tool's JSON result to conversation history as OpenAI's `tool` role, and immediately recurses back into `generate_agent_response()` so Cora's very next completion is grounded in the real transaction result rather than an assumption.
3. **Discriminated WebSocket Frames:** The same `/ws/stream` socket now carries two distinct payload shapes on one connection — binary frames (`message["bytes"]`) are forwarded to Deepgram as live audio, while JSON text frames (`message["text"]`) are inspected for a `mock_transcript` envelope and routed straight into the agent loop as if Deepgram had finalized that transcript. This lets `test_stream.py` exercise the full tool-calling path without a live microphone.
4. **Explicit Deepgram Listen Loop:** The Deepgram connection is opened lazily on the first captured audio byte (`channels=1, encoding="linear16", sample_rate=8000`), and `dg_connection.on(EventType.MESSAGE, on_message)` only *registers* a callback — nothing reads the Deepgram socket and actually emits that event until `dg_connection.start_listening()` is running. That call is spawned as its own `asyncio` task right after the handlers are registered, and cancelled in the `finally` block on disconnect. Without it, audio reaches Deepgram and gets transcribed fine, but the transcripts never make it back to the app.
5. **Settlement Idempotency Guard:** Deepgram can finalize one spoken instruction (e.g. a sentence with a mid-utterance pause) as multiple separate transcripts, each of which independently spawns its own conversational turn via `asyncio.create_task`. If more than one of those turns decides to call the settlement tool, an `asyncio.Lock` around a per-call `settlement_state` flag ensures only the first one that reaches it actually executes `process_account_settlement()` — every other turn is told the account is already settled instead of charging it again.

### Data Flow

```
[ Inbound Speech / mock_transcript ] ──► [ Deepgram STT / direct inject ] ──► [ Conversation History ]
                                                                                       │
                                                                                       ▼
                                                                          [ OpenAI GPT-4o + Tool Schema ]
                                                                                       │
                                                                        tool_calls?  ──┴── no ──► [ Aura TTS → caller ]
                                                                             │
                                                                            yes
                                                                             ▼
                                                          [ process_account_settlement() mock ledger ]
                                                                             │
                                                              (tool result appended to history)
                                                                             ▼
                                                          [ Recurse into OpenAI for confirmation reply ]
                                                                             │
                                                                             ▼
                                                                    [ Aura TTS → caller ]
```

#### Observed Console Output

The simulator waits for Cora's compliance greeting, then injects the settlement-acceptance transcript. The server halts normal reply generation, executes the tool, updates the mock ledger, and recurses back into OpenAI to verbalize the confirmed outcome:

```text
INFO:     WebSocket /ws/stream [accepted]
Web client connection established over voice socket loop.
Bidirectional Deepgram audio pipeline successfully initialized.

[Brain] Spinning up OpenAI stream engine...
[Brain] Cora Voice Response: Hello, Marcus Vance. This is Cora from Corafone Financial. This is an attempt to collect a debt by a debt collector. Any information obtained will be used for that purpose. How can I assist you today regarding your outstanding balance?

[STT Simulation] Caught user transcript: 'I accept the $300 settlement. Go ahead and charge it.'

[Brain] Spinning up OpenAI stream engine...
[DB Ledger Tool] Accessing account transaction logs for: Marcus Vance...
[DB Ledger Tool] SUCCESS: Deducted $300.00. Balance marked fully SETTLED.

[Brain] Spinning up OpenAI stream engine...
[Brain] Cora Voice Response: Thank you, Mr. Vance. I have successfully processed the payment of $300.00. Your account balance is now completely resolved, and your file has been marked as closed and settled. Have a wonderful rest of your day.

Web client disconnected from engine safely.
INFO:     connection closed
```

If Deepgram finalizes the same spoken agreement as more than one transcript, the settlement guard from item 5 above catches the duplicate rather than double-charging the account:

```text
[STT Voice Capture] Deepgram finalized: 'I accept the $300 settlement.'
[DB Ledger Tool] Accessing account transaction logs for: Marcus Vance...
[DB Ledger Tool] SUCCESS: Deducted $300.00. Balance marked fully SETTLED.

[STT Voice Capture] Deepgram finalized: 'Go ahead and charge it.'
[DB Ledger Tool] Settlement already processed this call — skipping duplicate charge.
```

---

## Phase 7: Real Supabase Integration

Phase 6's three tools (settlement, callback scheduling, payment plans) were mocks — they slept, fabricated an id, and persisted nothing. Phase 7 wires them to the Supabase database Phase 1 stood up: `app/db.py` owns a single `asyncpg` connection pool for the process lifetime (opened/closed via FastAPI's `lifespan`), and each tool in `app/tools.py` now reads/writes real rows instead of sleeping.

**Before running live calls after pulling this change**, run these migrations in the Supabase SQL Editor, in order, once each (after `script.sql`).

**1. [`migration_002_agent_tools.sql`](app/database/migration_002_agent_tools.sql)** -- adds the `payment_plans` and `scheduled_callbacks` tables, widens `accounts.status` to allow `PAYMENT_PLAN_ACTIVE`, and repoints the Phase 1 seed row's name from `'John'` to `'Marcus Vance'` so it matches the persona `app/config.py` actually uses:

```sql
-- 1. Payment plans now a valid disposition, not just SETTLED.
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_status_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_status_check
    CHECK (status IN ('ACTIVE', 'SETTLED', 'DO_NOT_CALL', 'DISPUTE', 'PAYMENT_PLAN_ACTIVE'));

-- 2. Installment plans booked by create_installment_payment_plan().
CREATE TABLE IF NOT EXISTS payment_plans (
    plan_id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    num_installments INT NOT NULL,
    amount_per_installment NUMERIC(10, 2) NOT NULL,
    total_amount NUMERIC(10, 2) NOT NULL,
    start_date_description TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Follow-up calls booked by schedule_followup_callback().
CREATE TABLE IF NOT EXISTS scheduled_callbacks (
    callback_id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(account_id) ON DELETE CASCADE,
    requested_time_description TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Phase 1 seeded this demo account as 'John'; the live agent's persona
-- (app/config.py) is 'Marcus Vance' -- repoint the same row rather than
-- seeding a second account.
UPDATE accounts SET customer_name = 'Marcus Vance' WHERE phone_number = '+15550199';
```

**2. [`migration_003_payment_plan_date.sql`](app/database/migration_003_payment_plan_date.sql)** -- replaces `payment_plans.start_date_description` (free text) with a real `start_date DATE` column, now that Cora is told today's date in her system prompt (rebuilt fresh per call via `config.build_system_prompt()`) and resolves phrases like "next Friday" into an absolute date herself before calling `offer_payment_plan`:

```sql
-- Assumes payment_plans has no rows yet (true unless a live payment-plan
-- call has already succeeded) -- ADD COLUMN ... NOT NULL fails otherwise;
-- TRUNCATE TABLE payment_plans first if it does.
ALTER TABLE payment_plans DROP COLUMN start_date_description;
ALTER TABLE payment_plans ADD COLUMN start_date DATE NOT NULL;
```

**3. [`migration_004_callback_datetime.sql`](app/database/migration_004_callback_datetime.sql)** -- same idea for callbacks: replaces `scheduled_callbacks.requested_time_description` (free text) with a real `callback_time TIMESTAMP` column, since Cora now resolves phrases like "tomorrow at 6 PM" into an absolute date-time before calling `schedule_callback`:

```sql
-- Assumes scheduled_callbacks has no rows yet -- ADD COLUMN ... NOT NULL
-- fails otherwise; TRUNCATE TABLE scheduled_callbacks first if it does.
ALTER TABLE scheduled_callbacks DROP COLUMN requested_time_description;
ALTER TABLE scheduled_callbacks ADD COLUMN callback_time TIMESTAMP NOT NULL;
```

Account identity is resolved once per call, server-side (`db.get_account_id_by_phone`, looked up by `config.CUSTOMER_PHONE_NUMBER` and stored on `CallSession.account_id`) rather than asked of the LLM — a phone call gives the model no reliable way to know its own database id, so the settlement/payment-plan tool schemas no longer even have an `account_id` parameter. Each tool call also writes a one-line disposition to `communication_logs` (e.g. "Settlement processed: tx_corafone_..., $300.00 charged.") so every real-world action taken on a call leaves an audit trail. If a DB write fails, `handle_function_call_request` now catches it and reports a spoken-friendly error back to the agent instead of the call silently hanging.

### Per-call transcript in Supabase Storage

`communication_logs` rows are great for structured, one-per-action records, but not for reading back a whole conversation. Instead, `app/session.py`'s `CallSession` accumulates a curated transcript (`log_lines`) as the call happens — conversation turns, barge-ins, and tool/billing actions, appended via `append_call_log` from `app/voice_agent.py` and `app/tools.py` — skipping routine internal events (`AgentAudioDone`, generic `History`, etc.). On teardown, `app/voice_agent.py`'s `teardown_session` uploads that transcript as a single text file to Supabase Storage via `app/storage.py`, at `communications/{account_id}/{call_datetime}/log.txt`.

**Manual setup required** (once, like the SQL migrations):
1. In the Supabase dashboard, create a **private** Storage bucket named `communications`.
2. Add `SUPABASE_URL` (project base URL) and `SUPABASE_SERVICE_ROLE_KEY` (Project Settings > API — service-role secret, backend-only, never exposed to the browser) to `.env`.

---

## Phase 8: Telemetry & FDCPA Audits

`voice_session_metrics` and `ai_evaluation_logs` were part of the Phase 1 schema from the start, but never written by a real call. Phase 8 wires both into the live path.

**Session metrics** (`app/voice_agent.py`'s `teardown_session`, written synchronously via a new `db.create_voice_session_metrics`): `total_duration_seconds` from `CallSession.call_started_at`; `barge_in_count`, incremented on every `UserStartedSpeaking` event (barge-ins were already detected for playback-clearing, just not counted before); `avg_latency_ms`, a new signal — the gap between the customer's last `ConversationText` turn and Cora's next `AgentStartedSpeaking` event, sampled per turn in `on_agent_message` and averaged at teardown; and `disposition_code` (`SETTLED` / `PAYMENT_PLAN_ACTIVE` / `CALLBACK_SCHEDULED` / `NO_ACTION`), derived from the same idempotency flags the three tools already maintain.

**Compliance audit** (new `app/audit.py`): an OpenAI structured-output judge (`EvaluationReport`) scores the real curated transcript (`session.log_lines`, the same one uploaded to Storage) and writes the result via `db.create_ai_evaluation_log`. Runs as a background task after the session-metrics row lands (`ai_evaluation_logs.session_id` is a foreign key into `voice_session_metrics`, so ordering matters), so a slower LLM judge call never delays call teardown.

**Deeper compliance + cost/error signals** (run [`migration_005_telemetry_extensions.sql`](app/database/migration_005_telemetry_extensions.sql) once, after `migration_004`): the judge now also scores `identity_verified_before_disclosure`, `prohibited_conduct_detected`, and `right_to_cease_honored` (`NULL` if the customer never asked to stop — not applicable that call) — these map more directly to what FDCPA actually cares about than tone alone. It also computes `judge_cost_usd`, a real dollar figure from `response.usage`'s token counts against `config.OPENAI_JUDGE_INPUT_COST_PER_1M`/`OPENAI_JUDGE_OUTPUT_COST_PER_1M` — this is the only LLM cost we can measure precisely, since the live conversation's OpenAI calls are intermediated by Deepgram and aren't visible to us (deliberately not estimated). Separately, `CallSession.error_count` counts caught exceptions during the call (failed tool calls, a failed Storage upload) and lands in `voice_session_metrics.error_count`.

FDCPA's call-frequency-limit rule (contact attempts per debtor per week) is out of scope here — it needs cross-call aggregation over `communication_logs`, not a per-call judge, and fits better alongside the future frontend/reporting work.

---

## Phase 9: Automated Test Suite

Every mocked check in the phases above was written as a one-off `python <<EOF ... EOF` snippet to verify a single change, then thrown away. Phase 9 turns that into a real, repeatable `pytest` suite under `tests/` — no real network/DB/LLM calls anywhere in it; everything below the process boundary (`asyncpg`, OpenAI, the WebSocket, Deepgram's agent connection) is mocked via fixtures in `tests/conftest.py`.

* `tests/test_db.py` - every `app/db.py` function issues the right SQL against the right parameters (mocked `asyncpg` pool/connection).
* `tests/test_tools.py` - the three tools' idempotency guards (only ever hit the DB once per call), correct dispatch, and the error-handling path (a failed tool call or a bad date from the LLM is caught and reported, not a crash).
* `tests/test_audit.py` - the compliance judge's cost computation and field pass-through, and that an OpenAI or DB failure during the audit never propagates.
* `tests/test_session.py` - `CallSession` defaults/uniqueness and the `append_call_log` transcript formatting.
* `tests/test_voice_agent.py` - barge-in counting, turn-latency sampling, and `teardown_session`'s disposition derivation + the FK-ordering gate (the compliance audit only fires after a successful `voice_session_metrics` write).

### Running it

```bash
pip install pytest pytest-asyncio
pytest
```

This is Layer 1 (unit tests, free and instant) of a broader test strategy. A real-Postgres integration layer is a natural future addition; Layer 3 (below) is already built.

### Layer 3: LLM-driven conversation scenarios

Layer 1 can't tell you whether Cora actually *behaves* correctly in a conversation -- only that the deterministic code around her does. `tests/scenarios/` drives a full text conversation between the real system prompt (`config.build_system_prompt()`), the real tool schemas, and the real `app/tools.py` execution functions (DB still mocked) against a scripted adversarial customer persona, entirely over OpenAI's chat completions API (no Deepgram/audio -- this tests decision logic, not voice transport). Each transcript is graded two ways: deterministic structural checks (`tests/scenarios/structural_checks.py` -- e.g. does any Cora turn contain more than one sentence, the exact regression class behind the mid-reply pause bug fixed earlier; does a tool ever get called more than once) and an LLM judge scored against that scenario's specific expected outcome (`tests/scenarios/judge.py`).

Seed scenarios (`tests/scenarios/definitions.py`): happy-path settlement, payment plan, and callback; a wrong-person identity check; vague/ambiguous agreement; a garbled/ambiguous date; discount-pressure beyond the 40% policy cap; and a stop-contact request.

This is the intended way to validate a prompt change (like the "ask for payment in full before laying out options" idea) against the full scenario set, not just one manual call.

**This costs real OpenAI tokens** (Collector + Consumer + judge calls, per trial, per scenario) and is inherently non-deterministic -- LLM output graded by another LLM. It's excluded from the default `pytest` run:

```bash
pytest -m scenario           # full suite
pytest -m scenario -k happy_path_settlement   # a single scenario
```

Each scenario runs 3 trials and asserts a pass *rate* (2/3), not a single pass/fail, since a lone red run isn't necessarily a regression -- worth a look, not an automatic hard failure. Known limitation: the judge only sees the transcript text, not which tools actually fired, so it can't independently verify "the tool was called exactly once" claims in an expected outcome -- that half is covered by the structural checks instead.

### CI

* [`.github/workflows/tests.yml`](.github/workflows/tests.yml) -- Layer 1, on every push/PR. Free and fast (everything mocked), so it runs with placeholder env var values -- no real secrets needed.
* [`.github/workflows/scenario-tests.yml`](.github/workflows/scenario-tests.yml) -- Layer 3, **manual only** (`workflow_dispatch`, triggered from the Actions tab), with an optional `scenario_filter` input to run just one scenario. Costs real OpenAI tokens, so it's not wired to run automatically -- it's there to demonstrate the suite or validate a prompt change on demand.

**Setup required**: add a real `OPENAI_API_KEY` repo secret (Settings > Secrets and variables > Actions) before running the Layer 3 workflow -- everything else in both workflows uses placeholder values since the DB/Deepgram/Supabase are all mocked in this suite.
