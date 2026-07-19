# Deployment — backend on Render, frontend on Vercel

Two deployments, one path each:

- **Backend** — FastAPI (WebSocket + REST) on **Render**, Starter tier (~$7/mo). Always-on, no spin-down between evaluator calls — no keep-alive ping needed. Cancel once evaluation is done.
- **Frontend** — `frontend/` static files on **Vercel**.
- **Dashboard** — **Vercel** too.

**This is a two-pass deploy.** The frontend needs the backend's URL; the backend needs the frontend's origin for CORS. Order: backend → frontend → update backend CORS → redeploy backend.

---

# Part 0 — Local development

Runs against the same Supabase project as the deployment — there's no separate local database. Three processes, each its own terminal.

## 0.1 Prerequisites

`.env` at repo root (gitignored):

```
DEEPGRAM_API_KEY
OPENAI_API_KEY
DATABASE_URL                 (session pooler URI)
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

`config.py` raises on startup if any is missing.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 0.2 Backend

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

`http://127.0.0.1:8000/health` → `{"status":"healthy"}`. Log should show "Supabase connection pool initialized."

## 0.3 Frontend

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://127.0.0.1:8080`. `localhost`/`127.0.0.1` count as a secure context, so mic access works over plain HTTP here — no HTTPS needed locally. Calls the demo account (`+15550199`) by default.

## 0.4 Dashboard

```bash
cd dashboard
npm install
npm run dev
```

Open `http://localhost:5173`.

## 0.5 Tests

```bash
pytest                 # Layer 1: unit tests, mocked, free and instant
pytest -m scenario      # Layer 3: real OpenAI calls, costs tokens
```

---

# Part A — Backend (Render)

## A1. `requirements.txt`

Already at repo root, versions pinned. Verify before you push:

```bash
python -m venv /tmp/deploycheck && /tmp/deploycheck/bin/pip install -r requirements.txt
/tmp/deploycheck/bin/python -c "import app.main"
```

If that import fails, the deploy will too.

## A2. CORS

Already environment-driven: `app/config.py` reads `EXTRA_CORS_ORIGINS` (comma-separated) and appends it to the localhost list. Adding the deployed frontend's origin is an env-var edit, not a code change — filled in during Part C.

**Note:** CORS governs HTTP calls only. Starlette doesn't enforce `Origin` on WebSocket connections, so cross-origin `wss://` works regardless — don't go hunting CORS config if the socket misbehaves; the cause will be something else.

## A3. Supabase project

Paste all of `script.sql` into the SQL Editor and run it. Collect:
   - `SUPABASE_URL` — Settings → API → Project URL
   - `SUPABASE_SERVICE_ROLE_KEY` — Settings → API → `service_role` (not `anon`)
   - `DATABASE_URL` — Settings → Database → Connection string → **Session pooler**

**The pooler is not optional.** Supabase's direct connection is IPv6-only and most PaaS hosts are IPv4; a direct URI fails with an error that won't tell you that's the problem.

Sanity check:

```sql
SELECT customer_name, current_balance, status FROM accounts;
-- John Callahan | 1000.00 | ACTIVE
```

## A4. Deploy to Render

1. [render.com](https://render.com) → New → Web Service → connect the repo.
2. Runtime: Python 3. Instance type: **Starter** (~$7/mo).
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. **Health Check Path: `/health`.** Required — with no static mount, `/` has no route and 404s. Render health-checking `/` by default would mark a working service unhealthy and restart it in a loop.

`--host 0.0.0.0` and `$PORT` are both required — a fixed port or localhost bind makes the service unreachable.

**Pin the Python version** with a `runtime.txt` at repo root (`python-3.12.7`), or Render picks a default that can change under you between deploys.

Starter is always-on, no spin-down between evaluator calls — no keep-alive ping needed. Cancel the service once evaluation is done.

## A5. Environment variables

Set these in Render's dashboard, not committed:

```
DEEPGRAM_API_KEY
OPENAI_API_KEY
DATABASE_URL                 (session pooler URI)
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
EXTRA_CORS_ORIGINS           (empty for now — filled in Part C)
TZ=America/New_York
```

**`TZ` is a correctness fix, not a nicety.** The server runs UTC by default; `build_system_prompt` tells the model "Today's date is {date.today()}" and `session.call_started_at` seeds the validator's `call_date`. A US evaluator calling at 8pm Eastern is calling at 1am UTC the next day, so the agent would announce tomorrow's date and a "pay today" agreement would get dated a day ahead. Setting `TZ` fixes it, and is more defensible anyway — FDCPA call-time rules are defined in the consumer's local time.

`config.py` raises on startup if any of the first five is missing, so a typo fails loudly in the deploy log rather than mysteriously at call time. Read the log after the first deploy.

Confirm `.env` is gitignored — already is (checked).

## A6. Verify before moving on

- `https://corafone-demo.onrender.com/health` → `{"status":"healthy"}`
- Deploy log contains "Supabase connection pool initialized." If not, `DATABASE_URL` is wrong (almost always direct-vs-pooler).

**Write down the backend URL.** Part B needs it.

---

# Part B — Frontend (Vercel)

`frontend/` is plain static files with no build step; the backend URL is injected via a `window.CORAFONE_API_BASE` line in `index.html` (already wired — `app.js` reads it), not compiled in.

## B1. Backend URL

In `frontend/index.html`, before the `app.js` script tag, `window.CORAFONE_API_BASE` is set by hostname: `localhost`/`127.0.0.1` gets the local backend, anything else gets the deployed one. No edit needed before pushing — this is what lets `npm run dev`/`python3 -m http.server` iteration stay local instead of silently spending Deepgram/OpenAI credit and mutating the production demo account.

```html
window.CORAFONE_API_BASE =
  (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? "http://127.0.0.1:8000"
    : "https://corafone-demo.onrender.com";
```

`app.js` derives `wss://` from it automatically. This matters more than it looks: browsers block microphone access on non-HTTPS origins, and refuse a `ws://` socket opened from an `https://` page — if the page loads but connecting does nothing, this is the first thing to check.

Once deployed, load the Vercel URL directly and confirm it's driving the deployed agent (not `127.0.0.1`) before calling B2 done.

## B2. Deploy to Vercel

1. [vercel.com](https://vercel.com) → New Project → import the repo.
2. **Root Directory:** `frontend`
3. **Framework Preset:** Other (no build step).
4. **Build Command:** leave empty. **Output Directory:** leave empty/default too -- Output Directory resolves *relative to Root Directory*, so setting it to `frontend` again would make Vercel look for `frontend/frontend/index.html`, which doesn't exist.

**Write down the frontend URL.**

## B3. Landing instruction

Evaluators arrive cold. One line above the connect button:

> Allow microphone access, then click Connect. Cora will speak first.

Without it, someone waits for a prompt that never comes and concludes the agent is broken.

---

# Part C — Close the loop

Back in Render's environment settings:

```
EXTRA_CORS_ORIGINS = https://your-frontend.vercel.app
```

No trailing slash — origins are scheme + host + port, and a trailing slash won't match.

Redeploy the backend. Until you do, the account lookup (`/api/dashboard/accounts`) will fail CORS and the page will look broken even though the backend is healthy.

---

# Part D — Verification

In this order; each catches a different failure:

1. Open the **frontend** URL. Page loads, no console errors.
2. Devtools → Network: `/api/dashboard/accounts` returns 200, not a CORS error. (If it fails, Part C isn't done or the origin has a typo.)
3. Click connect. Devtools → Network → WS: the socket shows `101 Switching Protocols`.
4. **Call it from your phone, on cellular, not wifi.** Your laptop has cached mic permissions and localhost exemptions that hide real problems. This is the test that counts.
5. **Multi-call sequence:** connect → settle in full → hang up → reconnect → confirm a clean $1,000 negotiation. The stateful test proves the SQL; this proves the wiring. Run it twice.
6. Confirm a transcript landed in the Supabase `communications` bucket and a row in `voice_session_metrics`.
7. **Try Safari, not just Chrome.** Safari is stricter about `AudioContext` and microphone permissions. If it works in Chrome and fails in Safari, you want to know now.

**Watch the WebSocket idle timeout.** Some proxies drop connections idle for ~60–100 seconds. Test it: connect, say nothing for two minutes, then speak — if the socket died, you'll need a keepalive ping.

**Supabase free projects pause after ~7 days of inactivity** — right at the edge of your reachability window, and independent of the Render tier (that only keeps the backend itself awake, not Supabase). Check the project after a few quiet days.

---

# Part E — API credit

`gpt-4o` judges every call. Top up Deepgram and OpenAI.

**Running dry mid-evaluation is the worst available failure** — the link works, the page loads, the agent silently doesn't respond, and it reads as a broken build rather than a billing problem.

---

# Dashboard (Vercel)

Already env-configurable (`dashboard/src/api.ts` reads `VITE_API_BASE` and `VITE_FRONTEND_BASE`).

1. [vercel.com](https://vercel.com) → New Project → import the repo.
2. **Root Directory:** `dashboard`
3. **Framework Preset:** Vite (auto-detected). Build command and output directory default correctly (`npm run build`, `dist`).
4. **Environment Variables:** `VITE_API_BASE` = backend URL, `VITE_FRONTEND_BASE` = frontend URL.

Then add the dashboard's Vercel URL to `EXTRA_CORS_ORIGINS` too (comma-separated) and redeploy the backend.

