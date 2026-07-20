"""Per-call state, passed explicitly to every handler."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import WebSocket

from .negotiation import NegotiationState, Offer, Verdict


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
    # has no way to see. Memoizes `negotiate` so a duplicate tool call
    # within one reasoning turn returns the cached verdict instead of
    # re-invoking the validator and re-spending the concession gate or
    # consuming a second candidate from `offered`.
    #
    # One cache for the one merged read-only tool (was two: cached_
    # validation_* for validate_consumer_proposal, cached_next_offer_* for
    # request_next_offer -- merged along with the tools themselves).
    # cached_negotiate_key covers every argument `negotiate` accepts (see
    # app/tools.py's _execute_negotiate_tool_call), so two genuinely
    # different calls in the same turn -- whichever arguments they use --
    # are never treated as a duplicate of each other.
    turn_id: int = 0
    cached_negotiate_turn: int | None = None
    cached_negotiate_key: tuple | None = None
    cached_negotiate_verdict: Verdict | None = None

    # The turn in which the concession gate last fired (COUNTER because
    # locked), and that verdict -- sibling to cached_negotiate_turn, but
    # deliberately separate: a second, *different* discount proposal within
    # the same turn must still be treated as locked, even though
    # negotiation_state itself now reads "unlocked" from the first call.
    gate_spent_turn: int | None = None
    gate_verdict_this_turn: Verdict | None = None

    # The most recent ACCEPT's exact Offer (payments included, uneven splits
    # and all) -- see app/tools.py's _execute_record_agreement_tool_call.
    # record_agreement's wire schema only carries total/count/cadence/date,
    # not a payments breakdown, so without this the write path always
    # re-derives an even split, silently overwriting an agreed uneven one
    # (e.g. "$600 today, $400 in two weeks" persisting as $500/$500). Most
    # recent ACCEPT wins; a later record_agreement that doesn't match this
    # offer's total/count/cadence/first-date falls back to the freshly
    # re-derived split rather than trusting a stale one.
    accepted_offer: Offer | None = None

    # Agreement idempotency guard (record at most once per call).
    agreement_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    agreement_recorded: bool = False
    # "SETTLED"/"PAYMENT_PLAN_ACTIVE" on a successful record_agreement, or
    # "ESCALATED_NO_AGREEMENT" set by app/tools.py when `negotiate` returns
    # a NO_AGREEMENT verdict (see app/negotiation.py's candidate
    # exhaustion). None means neither has happened yet this call.
    agreement_disposition: str | None = None

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

    # True from Deepgram's AgentStartedSpeaking until the browser has
    # actually finished PLAYING the queued audio -- lets app/voice_agent.py
    # tell a genuine barge-in (the customer interrupting mid-playback) from
    # a UserStartedSpeaking event that fires while the agent wasn't talking
    # at all (e.g. long after the agent finished, or a second event for the
    # same utterance). UserStartedSpeaking itself carries no such flag, and
    # clearing an already-empty audio buffer is harmless, so
    # `clear_audio_buffer` still fires unconditionally -- only the
    # barge_in_count/transcript line are gated on this.
    #
    # NOT cleared directly on AgentAudioDone -- that event fires when
    # Deepgram finishes SENDING audio bytes, not when the browser finishes
    # playing them (confirmed live: an 8-second gap between a disclosure's
    # AgentAudioDone and the customer actually speaking, well within
    # queued playback). Cleared instead by a delayed task scheduled from an
    # estimated playback end -- see app/voice_agent.py's
    # _schedule_agent_speaking_clear/_estimated_playback_remaining_seconds.
    agent_speaking: bool = False
    # Bytes of synthesized audio sent to the browser for the CURRENT
    # agent utterance, reset to 0 on every AgentStartedSpeaking -- the
    # input to the playback-end estimate (see above).
    audio_bytes_sent: int = 0
    # Wall-clock time the current utterance's AgentStartedSpeaking arrived
    # -- the anchor the playback-end estimate counts forward from.
    audio_segment_started_at: datetime | None = None
    # The pending "clear agent_speaking after estimated playback ends"
    # task, so a new AgentStartedSpeaking (a fresh utterance starting
    # before the previous one's estimated playback finished) or a real
    # barge-in (which clears agent_speaking immediately, definitively) can
    # cancel a stale one rather than let it fire later against a
    # since-changed state.
    agent_speaking_clear_task: asyncio.Task | None = None

    # Set on every assistant ConversationText, cleared by the next one --
    # True only while the CURRENT turn is the Mini-Miranda disclosure
    # (spoken verbatim, so an exact-content check is reliable). Read by a
    # genuine barge-in to decide whether the interrupted turn was the
    # disclosure specifically, not just "some turn."
    last_assistant_turn_was_mini_miranda: bool = False
    # Sticky for the rest of the call once set -- True if the Mini-Miranda
    # disclosure was ever genuinely interrupted before finishing. Read by
    # app/audit.py to deterministically force mini_miranda_passed to False
    # regardless of what the LLM judge concludes from the transcript text
    # alone: a disclosure the consumer didn't hear in full was not made,
    # not "probably fine" -- the same reasoning requires_manual_review
    # already gets from a stop-contact request (README's Compliance
    # notes), not left to an LLM to infer.
    mini_miranda_interrupted: bool = False


def append_call_log(session: "CallSession", tag: str, message: str) -> None:
    """Appends one curated, human-readable line to this call's transcript."""
    session.log_lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{tag}] {message}")
