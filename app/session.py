"""Per-call state, passed explicitly to every handler."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class CallSession:
    """State for one active call: WebSocket, tool idempotency guards, and
    the live Deepgram Voice Agent connection."""

    websocket: WebSocket

    # Settlement idempotency guard (only charge once per call).
    settlement_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    settlement_settled: bool = False
    settlement_transaction_id: str | None = None
    settlement_amount: float | None = None

    # Callback-scheduling idempotency guard.
    callback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    callback_scheduled: bool = False
    callback_id: str | None = None
    callback_requested_time: str | None = None

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
