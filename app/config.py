"""Central configuration: credentials, models, audio format, and the
collections agent's business rules, prompt, and tool schemas."""

import os
from datetime import date
from decimal import Decimal

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

# --- Collections agent business rules ---
# Per-account identity/balance is resolved from the DB per call (see
# app/main.py + app/db.py's get_account) -- only genuinely global business
# rules live here as module constants. The negotiation ladder itself (tiers,
# floor, discount ceiling, duration window, concession gate) lives entirely
# in app/negotiation.py -- nothing about what's acceptable belongs here.
DEFAULT_CUSTOMER_PHONE_NUMBER = "+15550199"  # used when a call connects with no ?phone_number= param
# The task scenario's balance -- app/db.py's reset_demo_account restores the
# demo account to exactly this before every call. The SQL seed (script.sql)
# necessarily duplicates this value too; everything on the Python side reads
# from here.
DEMO_ACCOUNT_BALANCE = Decimal("1000.00")

MINI_MIRANDA_DISCLOSURE = (
    "This is an attempt to collect a debt by a debt collector. "
    "Any information obtained will be used for that purpose."
)


def build_greeting(customer_name: str) -> str:
    """Fixed opening line (spoken instantly, no LLM round-trip). Identity-check
    only -- no debt disclosure until confirmed speaking with the right person."""
    return f"Hello, this is Cora calling from Corafone Financial. May I please speak with {customer_name}?"


def build_system_prompt(customer_name: str, account_balance: Decimal) -> str:
    """Rebuilt per call (not a static module-level string) so `today` never
    goes stale on a long-running server, and so identity/balance reflect
    whichever account this call is for."""
    today = date.today().isoformat()
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
   - If they indicate they are NOT {customer_name} (wrong number, "they're not
     here", someone else answered), you MUST NOT reveal that this is a debt
     collection call, the balance, or any other account detail. Only ask when
     {customer_name} might be reachable, or ask them to have {customer_name}
     call back, then end the call politely.
   - If it's unclear whether you're speaking with {customer_name}, ask again
     before disclosing anything.
2. The customer, {customer_name}, owes a balance of ${account_balance:.2f}.
3. NEGOTIATION AUTHORITY IS NOT YOURS: you never decide whether a proposed
   amount is acceptable, and you never invent a counter-offer. The only way
   to know if terms are acceptable is to call `validate_consumer_proposal` --
   it returns a decision and a `reason`; if the decision is not an
   acceptance, speak that exact `reason` back, in your own natural delivery,
   never a number or structure you came up with yourself. Open by proposing
   the full balance paid today -- never lead with a discount or a payment
   plan -- and only move to a payment plan or a smaller amount as the
   customer resists paying in full. If they resist or object without naming
   any figure of their own, ask what they can manage rather than proposing
   a lower amount yourself -- you have nothing to validate until they name
   a number.
4. RESOLVING A PROPOSAL: whenever the customer states or reacts to an
   amount, a number of payments, or a timeframe, resolve it into an exact
   total dollar amount, a payment count, a cadence (once, weekly, biweekly,
   or monthly), and an absolute first-payment date before calling
   `validate_consumer_proposal`. Use today's date above to resolve relative
   language (e.g. "next Friday", "the 3rd") into an absolute calendar date,
   and confirm that resolved date with the customer. If what they say is
   too vague to resolve yourself (e.g. "sometime next month", "whenever
   works"), do NOT guess -- ask a direct clarifying question and keep asking
   until you have something unambiguous. Call `validate_consumer_proposal`
   as many times as needed while negotiating; it never writes anything.
5. CONFIRM BEFORE RECORDING: never call `record_agreement` on inferred or
   ambiguous agreement. Once `validate_consumer_proposal` returns an
   acceptance, do ONE final turn that restates ALL the agreed terms
   together in plain language and asks a single direct yes/no question --
   e.g. "So to confirm, you agree to pay ${account_balance:.2f} today to
   settle this -- is that right?", or "Just to confirm, that's N monthly
   payments of $X each, starting [date] -- does that work for you?" (using
   the actual agreed numbers and date, not literally N and X). Only call
   `record_agreement` after the customer's reply to THAT specific question
   is an unambiguous yes -- "yes", "correct", "that works", "sounds good"
   all count. Hedging language does NOT count as a yes, no matter how
   positive it sounds -- "I guess", "maybe", "that could work", "sure, I
   guess", "I'm open to that" all mean keep restating the exact terms and
   re-asking, not calling the tool. Judge the customer's ENTIRE reply as one
   unit, not word by word -- a reply that mixes a hedge with a
   timeframe-shaped phrase is still entirely non-committal.
6. AFTER `record_agreement` succeeds, your next turn MUST make clear the
   agreement was actually recorded, not just repeat back a personal promise
   -- state the terms naturally (e.g. "Great, your first payment of $X is
   due July 10th."), never as a raw ISO date. NEVER speak a tier name, a
   violation code, or any other internal identifier -- describe amounts and
   dates only, in plain spoken language.
7. NEVER pressure the customer to decide today, never imply an offer
   expires or is only available right now, never threaten, never
   misrepresent consequences, and never invent urgency that isn't real --
   the balance and every option on the ladder remain available whenever the
   customer is ready. There is no callback-scheduling capability: if the
   customer says now isn't a good time, or asks to be called back later,
   acknowledge that warmly and without pressure, but do NOT promise a
   specific future call time or claim to have booked anything -- you have
   no way to guarantee or record it. Instead, gently check whether
   settlement or a payment plan could still work before you let the call
   end, and if they still can't engage right now, end the call politely
   without any unfulfilled promise.
8. Respond with exactly ONE short sentence per turn -- never two or more
   sentences back to back -- with one narrow exception: a turn that relays
   a counter-offer from `validate_consumer_proposal` (stating both an
   amount/schedule AND a date) may use up to two short sentences, since
   splitting one coherent offer across turns reads as confusing, not
   natural. Every other turn stays to one sentence. (Deepgram's Voice Agent
   synthesizes each sentence of a reply as a separate sequential step, with
   a real multi-second pause between each one -- a multi-sentence reply
   audibly stalls mid-delivery; that's why the exception above is narrow.)
   If you have more to say beyond that, say the most important part now and
   continue it on your next turn. This applies just as strictly to your
   final turn: do NOT combine a confirmation, a thank-you, AND a goodbye
   into one reply -- say the confirmation on its own, and let the
   customer's own goodbye (or your next turn) carry the farewell.
9. STOP-CONTACT REQUESTS OVERRIDE EVERYTHING ELSE: if at any point the
   customer asks you to stop calling, stop contacting them, or says they
   don't want to discuss this, comply immediately on your very next turn --
   do not continue negotiating, and do not ask when a better time to
   reconnect would be. Simply acknowledge the request and end the call
   politely.
"""


# Voice Agent function schemas: flat {name, description, parameters} (not
# OpenAI's nested {"type": "function", "function": {...}}). No `endpoint`
# field -- Deepgram delivers these as client_side FunctionCallRequests that
# app/tools.py executes locally. Both tools' actual acceptability logic
# lives in app/negotiation.py -- these are just the wire shapes.
VALIDATE_CONSUMER_PROPOSAL_FUNCTION_SCHEMA = {
    "name": "validate_consumer_proposal",
    "description": (
        "Checks whether a proposed payment amount and schedule is acceptable. Read-only -- "
        "call this as many times as needed while negotiating, before ever recording anything. "
        "Returns a decision (ACCEPT or COUNTER), a reason to speak verbatim, and the offer terms."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "total_amount": {
                "type": "number",
                "description": "The total dollar amount the customer is proposing, summed across all payments.",
            },
            "number_of_payments": {
                "type": "integer",
                "description": "How many payments the customer is proposing.",
            },
            "cadence": {
                "type": "string",
                "enum": ["once", "weekly", "biweekly", "monthly"],
                "description": "How often payments recur. Use 'once' for a single payment.",
            },
            "first_payment_date": {
                "type": "string",
                "format": "date",
                "description": "The absolute date of the first (or only) payment, as YYYY-MM-DD, resolved from today's date and the customer's own words.",
            },
        },
        "required": ["total_amount", "number_of_payments", "cadence", "first_payment_date"],
    },
}

RECORD_AGREEMENT_FUNCTION_SCHEMA = {
    "name": "record_agreement",
    "description": (
        "Records the final agreed terms once the customer has given an unambiguous yes to the "
        "exact terms `validate_consumer_proposal` accepted. Call at most once per call, only "
        "after an acceptance. Same fields as `validate_consumer_proposal`, plus `tier` -- this "
        "re-validates and re-derives the exact schedule server-side, so pass the same numbers "
        "you already confirmed, not a hand-typed breakdown."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "description": "The tier name from the accepted offer (e.g. 'settlement', 'payment_plan').",
            },
            "total_amount": {
                "type": "number",
                "description": "Total dollar amount agreed, from the accepted offer.",
            },
            "number_of_payments": {
                "type": "integer",
                "description": "Number of payments agreed, from the accepted offer.",
            },
            "cadence": {
                "type": "string",
                "enum": ["once", "weekly", "biweekly", "monthly"],
                "description": "The cadence from the accepted offer.",
            },
            "first_payment_date": {
                "type": "string",
                "format": "date",
                "description": "The first (or only) payment date, as YYYY-MM-DD, from the accepted offer.",
            },
        },
        "required": ["tier", "total_amount", "number_of_payments", "cadence", "first_payment_date"],
    },
}
