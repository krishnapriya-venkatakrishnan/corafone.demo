"""Backends for the two agent tools (validate_consumer_proposal,
record_agreement) and their dispatch from Deepgram's FunctionCallRequest.

Neither tool decides what's acceptable -- that's app/negotiation.py's job
entirely. This module's role is the boundary: convert the model's raw tool
arguments (strings, floats, whatever the LLM actually sent) into the
Decimal/date/enum types negotiation.py expects, treating anything
malformed the same way negotiation.py itself does -- pass it through
rather than raise, so `_is_sane` rejects it gracefully instead of this
module crashing first. Account identity (`session.account_id`) is always
resolved server-side at call start, never supplied by the LLM -- a phone
call gives the model no reliable way to know its own database id.
"""

import dataclasses
import json
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from deepgram.agent.v1.types import AgentV1SendFunctionCallResponse

from . import db, negotiation
from .session import CallSession, append_call_log

logger = logging.getLogger("corafone")


# --- Defensive conversion: garbage in, a value `_is_sane` will reject, not
# an exception this module has to catch itself. ---
def _safe_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value


def _safe_int(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _safe_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return value


def _safe_cadence(value):
    try:
        return negotiation.Cadence(value)
    except ValueError:
        return value


def _offer_to_dict(offer: negotiation.Offer) -> dict:
    return {
        "tier": offer.tier.value,
        "total": str(offer.total),
        "payments": [str(p) for p in offer.payments],
        "dates": [d.isoformat() for d in offer.dates],
        "cadence": offer.cadence.value,
    }


def _verdict_to_tool_result(verdict: negotiation.Verdict) -> dict:
    """Never includes `violations` -- those are log-only, see
    negotiation.Verdict's docstring; the model must never see or speak them."""
    offer = verdict.accepted_offer or verdict.counter_offer
    return {
        "decision": verdict.decision,
        "reason": verdict.reason,
        "offer": _offer_to_dict(offer) if offer else None,
    }


def _proposal_from_args(args: dict) -> negotiation.Proposal:
    return negotiation.Proposal(
        total=_safe_decimal(args.get("total_amount")),
        number_of_payments=_safe_int(args.get("number_of_payments")),
        cadence=_safe_cadence(args.get("cadence")),
        first_payment_date=_safe_date(args.get("first_payment_date")),
    )


# --- validate_consumer_proposal: read-only, callable repeatedly ---
async def _execute_validate_proposal_tool_call(args: dict, session: CallSession) -> dict:
    proposal = _proposal_from_args(args)
    key = (proposal.total, proposal.number_of_payments, proposal.cadence, proposal.first_payment_date)

    if session.cached_validation_turn == session.turn_id and session.cached_validation_key == key:
        logger.info("Duplicate tool call this turn -- returning the cached verdict.")
        return _verdict_to_tool_result(session.cached_validation_verdict)

    balance = Decimal(str(session.account_balance))
    is_discount_ask = isinstance(proposal.total, Decimal) and proposal.total < balance

    if session.gate_spent_turn == session.turn_id and is_discount_ask:
        # The gate already fired once this turn -- a second, *different*
        # discount ask in the same reasoning turn must not benefit from
        # negotiation_state now reading "unlocked" from that first call.
        # The consumer only said one thing this turn; reuse that answer.
        logger.info("Gate already spent this turn -- reusing this turn's gate verdict.")
        verdict = session.gate_verdict_this_turn
    else:
        call_date = session.call_started_at.date()
        counters_before = session.negotiation_state.discount_counters_issued
        verdict = negotiation.validate_proposal(balance, proposal, call_date, session.negotiation_state)
        if session.negotiation_state.discount_counters_issued > counters_before:
            session.gate_spent_turn = session.turn_id
            session.gate_verdict_this_turn = verdict

    session.cached_validation_turn = session.turn_id
    session.cached_validation_key = key
    session.cached_validation_verdict = verdict
    return _verdict_to_tool_result(verdict)


# --- record_agreement: the single write, re-validated server-side ---
async def _persist_agreement(account_id: int, offer: negotiation.Offer) -> None:
    """A single payment closes the account now (mirrors the mock ledger's
    old settlement behavior); more than one is a schedule of future
    payments (mirrors the old payment-plan behavior). `amount_per_installment`
    is the average, not the first payment, so "N x $X" always equals the
    total even for an uneven split; `payments_breakdown` keeps the exact
    amounts alongside it."""
    if len(offer.payments) == 1:
        await db.apply_settlement(account_id)
    else:
        average = (offer.total / len(offer.payments)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        breakdown = ",".join(str(p) for p in offer.payments)
        await db.create_payment_plan(
            account_id, len(offer.payments), average, offer.total, offer.dates[0], breakdown
        )
    await db.log_communication(
        account_id,
        f"Agreement recorded ({offer.tier.value}): ${offer.total} total across "
        f"{len(offer.payments)} payment(s) starting {offer.dates[0].isoformat()}.",
    )


async def _execute_record_agreement_tool_call(args: dict, session: CallSession) -> dict:
    async with session.agreement_lock:
        if session.agreement_recorded:
            logger.info("Agreement already recorded this call.")
            return {"status": "already_recorded"}

        proposal = _proposal_from_args(args)
        balance = Decimal(str(session.account_balance))
        call_date = session.call_started_at.date()

        # Re-validate against a *copy* of the negotiation state: a rejected
        # write must not spend the concession gate. If it mutated the live
        # state, a hallucinated or malformed record_agreement call would
        # silently unlock the next real discount request for free.
        state_for_check = dataclasses.replace(session.negotiation_state)
        verdict = negotiation.validate_proposal(balance, proposal, call_date, state_for_check)

        if verdict.decision != "ACCEPT":
            logger.warning("record_agreement refused: proposed terms did not validate.")
            return {
                "status": "rejected",
                "reason": verdict.reason,
                "offer": _offer_to_dict(verdict.counter_offer) if verdict.counter_offer else None,
            }

        offer = verdict.accepted_offer
        await _persist_agreement(session.account_id, offer)

        session.agreement_recorded = True
        session.agreement_disposition = "SETTLED" if len(offer.payments) == 1 else "PAYMENT_PLAN_ACTIVE"
        append_call_log(
            session, "Billing",
            f"Agreement recorded: {offer.tier.value}, ${offer.total} total across "
            f"{len(offer.payments)} payment(s) starting {offer.dates[0].isoformat()}.",
        )

        return {"status": "success", **_offer_to_dict(offer)}


# --- Dispatch ---
_FUNCTION_CALL_HANDLERS = {
    "validate_consumer_proposal": _execute_validate_proposal_tool_call,
    "record_agreement": _execute_record_agreement_tool_call,
}


async def handle_function_call_request(message, session: CallSession) -> None:
    """Executes each client_side function Deepgram asked for and reports
    the result back over the same connection."""
    for function_call in message.functions:
        handler = _FUNCTION_CALL_HANDLERS.get(function_call.name)
        if handler is None:
            logger.warning("Ignoring unknown function call request: %s", function_call.name)
            continue

        try:
            args = json.loads(function_call.arguments)
            result = await handler(args, session)
        except Exception:
            logger.exception("Tool call '%s' failed.", function_call.name)
            session.error_count += 1
            result = {
                "status": "error",
                "message": "That didn't go through due to a system issue -- let's try again.",
            }

        await session.agent_connection.send_function_call_response(
            AgentV1SendFunctionCallResponse(
                id=function_call.id,
                name=function_call.name,
                content=json.dumps(result),
            )
        )
