"""Per-call state, passed explicitly to every handler."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import WebSocket

from .negotiation import NegotiationState, Verdict


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
    # asyncpg returns NUMERIC columns as Decimal natively -- app/main.py
    # assigns account["current_balance"] straight through, no float cast,
    # so this is never a lossy float round-trip on money.
    account_balance: Decimal | None = None

    # Owned per app/negotiation.py's NegotiationState contract -- persists
    # for the whole call, mutated by app/negotiation.py's validate_proposal.
    negotiation_state: NegotiationState = field(default_factory=NegotiationState)

    # Bumped once per new customer utterance (app/voice_agent.py's
    # ConversationText handling) -- the turn boundary negotiation.py itself
    # has no way to see. Memoizes validate_consumer_proposal so a duplicate
    # tool call within one reasoning turn returns the cached verdict instead
    # of re-invoking the validator and re-spending the concession gate.
    turn_id: int = 0
    cached_validation_turn: int | None = None
    cached_validation_key: tuple | None = None
    cached_validation_verdict: Verdict | None = None

    # The turn in which the concession gate last fired (COUNTER because
    # locked), and that verdict -- sibling to cached_validation_turn, but
    # deliberately separate: a second, *different* discount proposal within
    # the same turn must still be treated as locked, even though
    # negotiation_state itself now reads "unlocked" from the first call.
    gate_spent_turn: int | None = None
    gate_verdict_this_turn: Verdict | None = None

    # Agreement idempotency guard (record at most once per call).
    agreement_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    agreement_recorded: bool = False
    agreement_disposition: str | None = None  # "SETTLED" or "PAYMENT_PLAN_ACTIVE", set on success

    # Deepgram Voice Agent connection, opened on connect.
    agent_context: Any = None
    agent_connection: Any = None
    agent_listen_task: asyncio.Task | None = None

    # Curated per-call transcript, uploaded to Supabase Storage on teardown
    # (see app/voice_agent.py's teardown_session and append_call_log below).
    call_started_at: datetime = field(default_factory=datetime.now)
    log_lines: list[str] = field(default_factory=list)

    # Telemetry, written to voice_session_metrics on teardown. Each sample
    # is Deepgram's own LatencyReport.total_latency for a turn (see
    # app/voice_agent.py's on_agent_message), not locally measured.
    barge_in_count: int = 0
    latency_samples_ms: list[float] = field(default_factory=list)
    error_count: int = 0


def append_call_log(session: "CallSession", tag: str, message: str) -> None:
    """Appends one curated, human-readable line to this call's transcript."""
    session.log_lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{tag}] {message}")
