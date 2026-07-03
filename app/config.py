"""Central configuration: credentials, models, audio format, and the
collections agent's business rules, prompt, and tool schemas."""

import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# --- API credentials ---
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")  # Supabase session pooler URI
SUPABASE_URL = os.getenv("SUPABASE_URL")  # Supabase project base URL (Storage REST API)
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # Storage uploads (app/storage.py)

_REQUIRED_ENV_VARS = {
    "DEEPGRAM_API_KEY": DEEPGRAM_API_KEY,
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "DATABASE_URL": DATABASE_URL,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_SERVICE_ROLE_KEY,
}
_missing_env_vars = [name for name, value in _REQUIRED_ENV_VARS.items() if not value]
if _missing_env_vars:
    raise RuntimeError(
        f"Missing required environment variable(s): {', '.join(_missing_env_vars)}. "
        "Check your .env file."
    )

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Service identity ---
APP_TITLE = "Corafone Voice Gateway"
WS_ROUTE_PATH = "/ws/stream"

# --- Dashboard (app/dashboard_api.py) ---
DASHBOARD_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",  # Vite dev server (dashboard/)
    "http://localhost:8080", "http://127.0.0.1:8080",  # frontend/'s python -m http.server
]

# --- Models ---
OPENAI_MODEL = "gpt-4o-mini"  # LLM used for reasoning (via Deepgram's think.provider)
OPENAI_JUDGE_MODEL = "gpt-4o"  # post-call FDCPA compliance audit (app/audit.py)
DEEPGRAM_AGENT_STT_MODEL = "flux-general-en"  # STT + turn detection
DEEPGRAM_TTS_MODEL = "aura-2-harmonia-en"  # TTS voice: empathetic, calm, professional

# gpt-4o list pricing as of this writing -- update if OpenAI's pricing changes.
# Used to compute the judge's real per-call cost (app/audit.py); the live
# conversation's cost isn't measurable from our side (Deepgram intermediates
# those OpenAI calls), so it's intentionally not estimated here.
OPENAI_JUDGE_INPUT_COST_PER_1M = 2.50
OPENAI_JUDGE_OUTPUT_COST_PER_1M = 10.00

# --- Audio format (must match frontend/app.js exactly) ---
AUDIO_ENCODING = "linear16"
AUDIO_SAMPLE_RATE = 24000
AUDIO_CHANNELS = 1

# --- Mock backend latency (app/tools.py) ---
MOCK_LEDGER_LATENCY_SECONDS = 0.8      # settlement / payment plan
MOCK_SCHEDULING_LATENCY_SECONDS = 0.3  # callback scheduling

# --- Collections agent business rules ---
# Per-account identity/balance is resolved from the DB per call (see
# app/main.py + app/db.py's get_account) -- only genuinely global business
# rules live here as module constants.
DEFAULT_CUSTOMER_PHONE_NUMBER = "+15550199"  # used when a call connects with no ?phone_number= param
MAX_SETTLEMENT_DISCOUNT_PERCENT = 40

MIN_INSTALLMENTS = 2  # payment plans cover the full balance, no discount
MAX_INSTALLMENTS = 6

MINI_MIRANDA_DISCLOSURE = (
    "This is an attempt to collect a debt by a debt collector. "
    "Any information obtained will be used for that purpose."
)


def minimum_settlement_amount(account_balance: float) -> float:
    return round(account_balance * (1 - MAX_SETTLEMENT_DISCOUNT_PERCENT / 100), 2)


def build_greeting(customer_name: str) -> str:
    """Fixed opening line (spoken instantly, no LLM round-trip). Identity-check
    only -- no debt disclosure until confirmed speaking with the right person."""
    return f"Hello, this is Cora calling from Corafone Financial. May I please speak with {customer_name}?"


def build_system_prompt(customer_name: str, account_balance: float) -> str:
    """Rebuilt per call (not a static module-level string) so `today` never
    goes stale on a long-running server, and so identity/balance reflect
    whichever account this call is for."""
    today = date.today().isoformat()
    minimum_settlement = minimum_settlement_amount(account_balance)
    return f"""You are Cora, an automated outbound voice collection agent for Corafone Financial.
Your tone must remain highly professional, warm, and genuinely empathetic -- never pushy or forceful.
Today's date is {today}.

CRITICAL RULES:
1. IDENTITY VERIFICATION (must happen before any debt disclosure): your opening
   greeting has already asked to speak with {customer_name}, without stating why
   you're calling. On your very first turn, react to how the person responded:
   - If they confirm they ARE {customer_name} (e.g. "speaking", "yes, that's me"),
     state the mandatory legal disclosure now, verbatim, as your entire turn:
     "{MINI_MIRANDA_DISCLOSURE}" Continue the collections conversation naturally
     after that.
   - If they indicate they are NOT {customer_name} (wrong number, "he's not
     here", someone else answered), you MUST NOT reveal that this is a debt
     collection call, the balance, or any other account detail. Only ask when
     {customer_name} might be reachable, or ask them to have him call back,
     then end the call politely.
   - If it's unclear whether you're speaking with {customer_name}, ask again
     before disclosing anything.
2. The customer, {customer_name}, owes a balance of ${account_balance:.2f}.
3. Offer BOTH of these as genuine options once discussing the balance -- do not
   favor or push one over the other -- and then explicitly ask the customer
   which they'd prefer (e.g. "Would you like to settle today, set up a payment
   plan, or would another time work better for a callback?"). Don't just
   describe the options and wait; actively invite a decision.
   a. A one-time settlement discount up to {MAX_SETTLEMENT_DISCOUNT_PERCENT}%
      (${minimum_settlement:.2f} minimum total) if paid today.
   b. An installment plan covering the full ${account_balance:.2f} balance over
      {MIN_INSTALLMENTS}-{MAX_INSTALLMENTS} monthly payments, for customers who
      can't pay a lump sum today. If they choose this, also agree on WHEN the
      first payment is due before calling the tool (rule 4).
4. CONFIRM BEFORE ACTING: never call `process_account_settlement`,
   `offer_payment_plan`, or `schedule_callback` on inferred or ambiguous
   agreement. First restate the exact terms in plain language and ask a direct
   yes/no question -- e.g. "So to confirm, you agree to pay ${minimum_settlement:.2f}
   today to settle this -- is that right?", or "Just to confirm, that's N monthly
   payments of $X each, starting [date] -- does that work for you?" (using the
   actual agreed numbers and date, not literally N and X), or "So I'll call you
   back tomorrow at 6 PM -- does that work?" Only call the matching tool after
   the customer clearly says yes to those exact terms. For a payment plan, use
   today's date above to resolve whatever the customer said (e.g. "next Friday")
   into an absolute calendar date, confirm that resolved date in the yes/no
   question, and pass that same absolute date to `offer_payment_plan`. Likewise,
   for a callback, resolve the customer's requested day/time (e.g. "tomorrow at
   6 PM") into an absolute date and time using today's date above, and pass that
   same absolute date-time to `schedule_callback`.
5. AFTER `offer_payment_plan` succeeds, your next turn MUST state when the
   payment schedule starts, spoken naturally (e.g. "Great, your first payment
   of $X is due July 10th."), never as a raw ISO date -- not just confirm the
   plan was created.
6. NEVER pressure the customer to decide today, and never imply the settlement
   discount expires or is only available right now -- it remains available
   whenever they're ready, including at a rescheduled callback. If the customer
   says now isn't a good time, or asks to be contacted later, accept that
   immediately and warmly -- confirm the time (rule 4) and call
   `schedule_callback` instead of continuing to make the case for paying today.
7. Respond with exactly ONE short sentence per turn -- never two or more sentences
   back to back. (Deepgram's Voice Agent synthesizes and speaks each sentence of a
   reply as a separate sequential step, with a real multi-second pause between each
   one -- a multi-sentence reply audibly stalls mid-delivery. A single sentence per
   turn has no internal boundary to stall on, and matches how a real phone
   conversation actually flows turn by turn anyway.) If you have more than one
   thing to say, say the most important part now and continue it on your next turn.
"""

# Voice Agent function schemas: flat {name, description, parameters} (not
# OpenAI's nested {"type": "function", "function": {...}}). No `endpoint`
# field -- Deepgram delivers these as client_side FunctionCallRequests that
# app/tools.py executes locally.
def build_settlement_function_schema(account_balance: float) -> dict:
    minimum_settlement = minimum_settlement_amount(account_balance)
    return {
        "name": "process_account_settlement",
        "description": "Executes an immediate collection settlement deduction against the user account database balance.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": f"The exact settlement dollar total agreed upon (minimum {minimum_settlement:.2f}).",
                },
            },
            "required": ["amount"],
        },
    }

SCHEDULE_CALLBACK_FUNCTION_SCHEMA = {
    "name": "schedule_callback",
    "description": "Books a follow-up call at a specific date/time when the customer isn't available now or asks to be contacted later. Use this instead of continuing to press for a decision today.",
    "parameters": {
        "type": "object",
        "properties": {
            "callback_datetime": {
                "type": "string",
                "format": "date-time",
                "description": "The absolute date and time of the callback, as YYYY-MM-DDTHH:MM:SS (24-hour), resolved from today's date and the customer's own words (e.g. 'tomorrow at 6 PM').",
            },
        },
        "required": ["callback_datetime"],
    },
}

OFFER_PAYMENT_PLAN_FUNCTION_SCHEMA = {
    "name": "offer_payment_plan",
    "description": f"Sets up an installment payment plan covering the full account balance over {MIN_INSTALLMENTS}-{MAX_INSTALLMENTS} monthly payments, for customers who can't pay a lump-sum settlement today.",
    "parameters": {
        "type": "object",
        "properties": {
            "num_installments": {
                "type": "integer",
                "description": f"Number of monthly installments ({MIN_INSTALLMENTS}-{MAX_INSTALLMENTS}).",
            },
            "amount_per_installment": {
                "type": "number",
                "description": "Dollar amount per installment, agreed with the customer.",
            },
            "start_date": {
                "type": "string",
                "format": "date",
                "description": "The absolute date of the first installment, as YYYY-MM-DD, resolved from today's date and the customer's own words (e.g. 'next Friday').",
            },
        },
        "required": ["num_installments", "amount_per_installment", "start_date"],
    },
}
