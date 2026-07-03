"""Supabase/Postgres access for the live call tools (app/tools.py). Owns one
connection pool for the process lifetime; nothing here knows about Deepgram
or the agent protocol -- see app/voice_agent.py for that side."""

import logging
from datetime import date, datetime

import asyncpg

from . import config

logger = logging.getLogger("corafone")

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    logger.info("Supabase connection pool initialized.")


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()
        logger.info("Supabase connection pool closed.")


async def get_account_id_by_phone(phone_number: str) -> int:
    """Resolves the demo account once per call. Not LLM-supplied -- see
    app/tools.py for why account identity is always server-resolved."""
    row = await _pool.fetchrow(
        "SELECT account_id FROM accounts WHERE phone_number = $1", phone_number
    )
    if row is None:
        raise ValueError(f"No account found for phone number {phone_number!r}.")
    return row["account_id"]


async def apply_settlement(account_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE accounts SET current_balance = 0, status = 'SETTLED' WHERE account_id = $1",
            account_id,
        )


async def create_payment_plan(
    account_id: int,
    num_installments: int,
    amount_per_installment: float,
    total_amount: float,
    start_date: date,
) -> None:
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO payment_plans
                    (account_id, num_installments, amount_per_installment, total_amount, start_date)
                VALUES ($1, $2, $3, $4, $5)
                """,
                account_id,
                num_installments,
                amount_per_installment,
                total_amount,
                start_date,
            )
            await conn.execute(
                "UPDATE accounts SET status = 'PAYMENT_PLAN_ACTIVE' WHERE account_id = $1",
                account_id,
            )


async def create_scheduled_callback(account_id: int, callback_time: datetime) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO scheduled_callbacks (account_id, callback_time) VALUES ($1, $2)",
            account_id,
            callback_time,
        )


async def log_communication(account_id: int, content: str) -> None:
    """One structured row per tool disposition (settlement/callback/payment
    plan) -- always an outbound action Cora took. The full turn-by-turn
    transcript lives in Supabase Storage instead (see app/storage.py)."""
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO communication_logs (account_id, channel, direction, content) VALUES ($1, 'VOICE', 'OUTBOUND', $2)",
            account_id,
            content,
        )


async def create_voice_session_metrics(
    session_id: str,
    account_id: int,
    total_duration_seconds: int,
    avg_latency_ms: int,
    barge_in_count: int,
    disposition_code: str,
    error_count: int,
    transcript_path: str | None = None,
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO voice_session_metrics
                (session_id, account_id, total_duration_seconds, avg_latency_ms, barge_in_count,
                 disposition_code, error_count, transcript_path)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            session_id,
            account_id,
            total_duration_seconds,
            avg_latency_ms,
            barge_in_count,
            disposition_code,
            error_count,
            transcript_path,
        )


async def create_ai_evaluation_log(
    session_id: str,
    mini_miranda_passed: bool,
    pii_redacted_correctly: bool,
    hallucination_detected: bool,
    identity_verified_before_disclosure: bool,
    prohibited_conduct_detected: bool,
    right_to_cease_honored: bool | None,
    tone_score: int,
    judge_reasoning: str,
    judge_cost_usd: float,
) -> None:
    """Requires a voice_session_metrics row for `session_id` to already exist
    (foreign key) -- see app/voice_agent.py's teardown_session for ordering."""
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ai_evaluation_logs
                (session_id, mini_miranda_passed, pii_redacted_correctly, hallucination_detected,
                 identity_verified_before_disclosure, prohibited_conduct_detected, right_to_cease_honored,
                 tone_score, judge_reasoning, judge_cost_usd)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            session_id,
            mini_miranda_passed,
            pii_redacted_correctly,
            hallucination_detected,
            identity_verified_before_disclosure,
            prohibited_conduct_detected,
            right_to_cease_honored,
            tone_score,
            judge_reasoning,
            judge_cost_usd,
        )


# --- Dashboard reads (app/dashboard_api.py) ---
async def get_account(phone_number: str) -> dict | None:
    row = await _pool.fetchrow(
        "SELECT account_id, customer_name, phone_number, current_balance, status "
        "FROM accounts WHERE phone_number = $1",
        phone_number,
    )
    return dict(row) if row else None


async def get_compliance_summary() -> dict:
    row = await _pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_calls,
            AVG(CASE WHEN mini_miranda_passed THEN 1.0 ELSE 0.0 END) AS mini_miranda_pass_rate,
            AVG(tone_score) AS avg_tone_score,
            SUM(CASE WHEN hallucination_detected THEN 1 ELSE 0 END) AS hallucination_count,
            SUM(CASE WHEN prohibited_conduct_detected THEN 1 ELSE 0 END) AS prohibited_conduct_count,
            SUM(judge_cost_usd) AS total_judge_cost_usd
        FROM ai_evaluation_logs
        """
    )
    return dict(row)


async def get_calls() -> list[dict]:
    """Call history: voice_session_metrics left-joined to ai_evaluation_logs
    (the audit runs in the background, so it may not have landed yet -- or
    may have failed -- for the most recent call)."""
    rows = await _pool.fetch(
        """
        SELECT
            vsm.session_id, vsm.account_id, vsm.created_at, vsm.total_duration_seconds,
            vsm.avg_latency_ms, vsm.barge_in_count, vsm.disposition_code, vsm.error_count,
            vsm.transcript_path,
            ael.mini_miranda_passed, ael.pii_redacted_correctly, ael.hallucination_detected,
            ael.identity_verified_before_disclosure, ael.prohibited_conduct_detected,
            ael.right_to_cease_honored, ael.tone_score, ael.judge_reasoning, ael.judge_cost_usd
        FROM voice_session_metrics vsm
        LEFT JOIN ai_evaluation_logs ael ON ael.session_id = vsm.session_id
        ORDER BY vsm.created_at DESC
        """
    )
    return [dict(row) for row in rows]


async def get_active_payment_plans() -> list[dict]:
    rows = await _pool.fetch(
        "SELECT plan_id, account_id, num_installments, amount_per_installment, total_amount, "
        "start_date, status, created_at FROM payment_plans WHERE status = 'ACTIVE' "
        "ORDER BY start_date ASC"
    )
    return [dict(row) for row in rows]


async def get_pending_callbacks() -> list[dict]:
    rows = await _pool.fetch(
        "SELECT callback_id, account_id, callback_time, status, created_at "
        "FROM scheduled_callbacks WHERE status = 'PENDING' ORDER BY callback_time ASC"
    )
    return [dict(row) for row in rows]


async def get_call_transcript_path(session_id: str) -> str | None:
    row = await _pool.fetchrow(
        "SELECT transcript_path FROM voice_session_metrics WHERE session_id = $1", session_id
    )
    return row["transcript_path"] if row else None
