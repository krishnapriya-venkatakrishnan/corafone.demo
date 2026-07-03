"""Per-call state, passed explicitly to every handler."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket


@dataclass
class CallSession:
    """State for one active call: WebSocket, tool idempotency guards, and
    the live Deepgram Voice Agent connection."""

    websocket: WebSocket

    # Unique per call -- ties voice_session_metrics to ai_evaluation_logs
    # (see app/audit.py and app/voice_agent.py's teardown_session).
    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:10]}")

    # Resolved once at call start (app/db.py), the real Supabase account this
    # call is for -- never taken from the LLM (see app/tools.py for why).
    account_id: int | None = None
    customer_name: str | None = None
    account_balance: float | None = None

    # Settlement idempotency guard (only charge once per call).
    settlement_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    settlement_settled: bool = False
    settlement_transaction_id: str | None = None
    settlement_amount: float | None = None

    # Callback-scheduling idempotency guard.
    callback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    callback_scheduled: bool = False
    callback_id: str | None = None
    callback_time: str | None = None

    # Payment-plan idempotency guard.
    payment_plan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    payment_plan_created: bool = False
    payment_plan_id: str | None = None
    payment_plan_installments: int | None = None
    payment_plan_amount_per_installment: float | None = None
    payment_plan_start_date: str | None = None

    # Deepgram Voice Agent connection, opened on connect.
    agent_context: Any = None
    agent_connection: Any = None
    agent_listen_task: asyncio.Task | None = None

    # Curated per-call transcript, uploaded to Supabase Storage on teardown
    # (see app/voice_agent.py's teardown_session and append_call_log below).
    call_started_at: datetime = field(default_factory=datetime.now)
    log_lines: list[str] = field(default_factory=list)

    # Telemetry, written to voice_session_metrics on teardown.
    barge_in_count: int = 0
    last_user_turn_at: datetime | None = None
    latency_samples_ms: list[float] = field(default_factory=list)
    error_count: int = 0


def append_call_log(session: "CallSession", tag: str, message: str) -> None:
    """Appends one curated, human-readable line to this call's transcript."""
    session.log_lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{tag}] {message}")
