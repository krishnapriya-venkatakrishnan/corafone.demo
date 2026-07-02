# Corafone Voice Gateway

The Corafone automated voice engine, built up in phases. Phase 1 laid the data foundation (Supabase schema + an offline multi-agent simulation sandbox). Phase 2 added the live, real-time streaming core of the gateway. Phase 3 gave that gateway its conversational intelligence. Phase 4 closed the loop with real-time synthesized audio replies. Phase 5 gives the gateway a real, in-browser client to talk to.

## Project Structure

* `app/main.py` - The live web server core. Exposes a JSON health check endpoint and a persistent, bidirectional binary WebSocket pipeline (`/ws/stream`): incoming audio streams into Deepgram for transcription, finalized transcripts drive a non-blocking OpenAI GPT-4o chat loop, and the streamed reply text is chunked clause-by-clause into Deepgram Aura TTS, with the resulting audio bytes written straight back down the same socket.
* `app/simulation.py` - The offline sandbox script. Runs an automated turn-by-turn conversation between an AI Collector persona and an adversarial AI Consumer persona, passes the transcript to a GPT-4o auditing judge, and logs the structured metrics straight to Supabase.
* `app/database/script.sql` - Schema definitions and seed data for the Supabase tables (accounts, communication logs, session metrics, and AI evaluation logs).
* `index.html` - The Corafone Voice UI. A self-contained browser client (vanilla JS + inline Tailwind CSS) that captures microphone audio, downsamples it to 8000Hz mono PCM16, and streams it to `/ws/stream`, while playing back the agent's synthesized voice gaplessly through the Web Audio API. See [Phase 5: Browser Voice Client](#phase-5-browser-voice-client) below.
* `test_stream.py` - A localized client automation script used to simulate live network traffic. It streams mock PCM audio up to the server while a concurrent background task listens for and prints the synthesized audio bytes streaming back down.
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

With the audio gateway able to carry raw PCM in both directions, Phase 5 gives it a real client to talk to. [`index.html`](index.html) — the **Corafone Voice UI** — replaces `test_stream.py` as the actual client a person talks to. It's a single, self-contained file (vanilla JS + inline Tailwind CSS, no build step, no framework) that turns a browser tab into a live call with Cora using nothing but the Web Audio API and a raw binary WebSocket.

### UI

A minimalist, dark call screen: a central mic button that toggles connect/disconnect, animated pulsing rings and a breathing glow while the call is live, a status pill that reflects the live call state (`Disconnected` / `Connecting…` / `Listening…` / `Cora Speaking…`), and a running call timer.

### Capturing and Uploading Microphone Audio

1. `navigator.mediaDevices.getUserMedia` captures a mono mic stream (with echo cancellation, noise suppression, and AGC enabled). The `AudioContext` is deliberately constructed only after the user clicks the mic button, respecting the browser's user-gesture requirement for audio.
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
python3 -m http.server 8080
```

Then open `http://127.0.0.1:8080/index.html` in a browser and click the mic button to start a live call.
