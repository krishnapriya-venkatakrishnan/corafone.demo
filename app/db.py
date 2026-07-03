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
