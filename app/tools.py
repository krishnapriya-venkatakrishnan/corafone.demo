"""Backends for the two agent tools (negotiate, record_agreement) and
their dispatch from Deepgram's FunctionCallRequest.

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


def _safe_bool(value) -> bool:
    """discount_requested has no `_sanity_violation`-style backstop of its
    own (same reasoning as customer_capacity) -- a malformed value (a
    stray string, a number) is normalised to False here rather than
    passed through, since negotiate() branches directly on truthiness
    with no downstream check to catch a non-bool."""
    return value is True


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
    negotiation.Verdict's docstring; the model must never see or speak them.
    `minimum_payment` is included only when set (the floor was actually
    breached by something the consumer said -- either a live proposal's own
    payments, or a stated customer_capacity) -- see Verdict's docstring for
    why it isn't always present. `agent_note` is included only when set
    (never on ACCEPT or NO_AGREEMENT) -- machine-readable, for the agent to read and act on,
    never speak (see the prompt rule in app/config.py). `offer_summary` is
    included whenever there's an offer at all (ACCEPT or COUNTER) --
    pre-formatted for the agent to confirm from directly, see Verdict's
    docstring."""
    offer = verdict.accepted_offer or verdict.counter_offer
    result = {
        "decision": verdict.decision,
        "reason": verdict.reason,
        "offer": _offer_to_dict(offer) if offer else None,
    }
    if verdict.minimum_payment is not None:
        result["minimum_payment"] = str(verdict.minimum_payment)
    if verdict.agent_note:
        result["agent_note"] = verdict.agent_note
    if verdict.offer_summary is not None:
        result["offer_summary"] = verdict.offer_summary
    return result


def _format_tool_args(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())


def _format_tool_result(result: dict) -> str:
    """Human-readable, log-only summary of what a tool call actually
    authorized -- never spoken by the model, but this is what lets a
    reader (or the structural provenance check in
    tests/scenarios/structural_checks.py) verify a spoken figure against
    ground truth instead of trusting the transcript's prose."""
    if "decision" in result:  # negotiate
        line = f'{result["decision"]}: "{result.get("reason", "")}"'
        if result.get("agent_note"):
            line += f' [agent_note: {result["agent_note"]}]'
        return line
    status = result.get("status")
    if status == "success":
        n = len(result.get("payments") or [])
        return f'success: {result.get("tier")}, ${result.get("total")} total across {n} payment(s)'
    if status == "rejected":
        return f'rejected: "{result.get("reason", "")}"'
    return str(status)


def _log_tool_call(session: CallSession, name: str, args: dict, result: dict) -> None:
    """Curated into the same transcript uploaded to Supabase Storage and
    read by the compliance judge (app/audit.py) -- every dollar figure,
    date, and payment count Cora speaks should be traceable back to one of
    these lines or to the customer's own words (see G1/I1)."""
    append_call_log(session, "Tool", f"{name}({_format_tool_args(args)})")
    append_call_log(session, "Tool", f"-> {_format_tool_result(result)}")


def _proposal_from_args(args: dict) -> negotiation.Proposal:
    raw_payments = args.get("payments")
    payments = [_safe_decimal(p) for p in raw_payments] if isinstance(raw_payments, list) else None
    return negotiation.Proposal(
        total=_safe_decimal(args.get("total_amount")),
        number_of_payments=_safe_int(args.get("number_of_payments")),
        cadence=_safe_cadence(args.get("cadence")),
        first_payment_date=_safe_date(args.get("first_payment_date")),
        payments=payments,
    )


# --- negotiate: the single read-only tool, replacing validate_consumer_
# proposal and request_next_offer -- gpt-4o-mini could not reliably choose
# between the two (see app/negotiation.py's negotiate() docstring for the
# live failure that motivated the merge). Every argument is optional;
# app/negotiation.py's negotiate() resolves which of validate_proposal/
# request_next_offer applies from whichever ones are present. This
# module's job is unchanged from before the merge: the boundary (raw JSON
# -> typed args) and turn-scoping (caching, the gate-shopping guard). ---
def _negotiate_args_from_raw(args: dict) -> dict:
    raw_payments = args.get("payments")
    payments = [_safe_decimal(p) for p in raw_payments] if isinstance(raw_payments, list) else None
    raw_capacity = _safe_decimal(args["customer_capacity"]) if args.get("customer_capacity") is not None else None
    return {
        "total_amount": _safe_decimal(args["total_amount"]) if args.get("total_amount") is not None else None,
        "payments": payments,
        # Normalised to Decimal-or-None here (not passed through malformed,
        # unlike the other fields) -- customer_capacity has no
        # `_sanity_violation`-style backstop of its own in negotiation.py,
        # so a malformed value must be caught at this boundary instead of
        # ever reaching a bare `.quantize()` call downstream.
        "customer_capacity": raw_capacity if isinstance(raw_capacity, Decimal) else None,
        "cadence": _safe_cadence(args["cadence"]) if args.get("cadence") is not None else None,
        "first_payment_date": _safe_date(args["first_payment_date"]) if args.get("first_payment_date") is not None else None,
        "number_of_payments": _safe_int(args["number_of_payments"]) if args.get("number_of_payments") is not None else None,
        "discount_requested": _safe_bool(args.get("discount_requested")),
    }


def _resolved_discount_ask(total_amount, payments, discount_requested: bool, balance: Decimal) -> bool:
    """Answers "is this turn's call a discount ask" for the gate-shopping
    guard below, which is a turn-scoping concern that belongs in this
    module, not negotiation.py. Two ways a call can be one: an explicit
    `discount_requested=True` (always is, by definition), or a proposal
    whose resolved total is below balance -- that total derivation is
    negotiation.resolve_proposal_total, the exact same function
    negotiate()'s own step 1 calls, so the two can never drift apart (see
    that function's docstring for what drift here would actually break:
    the gate would become shoppable within a turn again)."""
    if discount_requested:
        return True
    total_amount = negotiation.resolve_proposal_total(total_amount, payments)
    return isinstance(total_amount, Decimal) and isinstance(balance, Decimal) and total_amount < balance


async def _execute_negotiate_tool_call(args: dict, session: CallSession) -> dict:
    resolved = _negotiate_args_from_raw(args)
    # Every supplied field is part of the key -- two calls in the same turn
    # with a different total, a different split, a different capacity, or
    # a different date are different requests, not a duplicate (mirrors
    # the pre-merge payments_key/capacity-key patterns, now unified).
    payments_key = tuple(resolved["payments"]) if resolved["payments"] is not None else None
    key = (
        resolved["total_amount"], payments_key, resolved["customer_capacity"],
        resolved["cadence"], resolved["first_payment_date"], resolved["number_of_payments"],
        resolved["discount_requested"],
    )

    if session.cached_negotiate_turn == session.turn_id and session.cached_negotiate_key == key:
        logger.info("Duplicate tool call this turn -- returning the cached verdict.")
        result = _verdict_to_tool_result(session.cached_negotiate_verdict)
        _log_tool_call(session, "negotiate", args, result)
        return result

    balance = Decimal(str(session.account_balance))
    is_discount_ask = _resolved_discount_ask(
        resolved["total_amount"], resolved["payments"], resolved["discount_requested"], balance
    )

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
        verdict = negotiation.negotiate(
            balance, call_date, session.negotiation_state,
            total_amount=resolved["total_amount"], payments=resolved["payments"],
            customer_capacity=resolved["customer_capacity"], cadence=resolved["cadence"],
            first_payment_date=resolved["first_payment_date"], number_of_payments=resolved["number_of_payments"],
            discount_requested=resolved["discount_requested"],
        )
        if session.negotiation_state.discount_counters_issued > counters_before:
            session.gate_spent_turn = session.turn_id
            session.gate_verdict_this_turn = verdict

    if verdict.decision == "NO_AGREEMENT":
        # No DB write here (this tool stays read-only) -- just a session
        # flag so teardown_session can derive the disposition and flag the
        # account for manual review. See app/negotiation.py's candidate
        # exhaustion (selection returning None).
        session.agreement_disposition = "ESCALATED_NO_AGREEMENT"
    elif verdict.decision == "ACCEPT":
        # Remembered so record_agreement (whose wire schema carries no
        # payments breakdown) can persist this offer's exact split instead
        # of re-deriving an even one -- see CallSession.accepted_offer and
        # _execute_record_agreement_tool_call below. Most recent ACCEPT
        # wins, matching "most recent statement wins" everywhere else in
        # this module.
        session.accepted_offer = verdict.accepted_offer

    session.cached_negotiate_turn = session.turn_id
    session.cached_negotiate_key = key
    session.cached_negotiate_verdict = verdict
    result = _verdict_to_tool_result(verdict)
    _log_tool_call(session, "negotiate", args, result)
    return result


def _offer_terms_match(a: negotiation.Offer, b: negotiation.Offer) -> bool:
    """total/payment-count/cadence/first-date equivalence -- deliberately
    NOT a full equality check (tier can legitimately differ if selection
    reclassified the same shape, and the two offers' `dates` beyond the
    first are already implied identical once cadence/count/first-date
    match, since _payment_dates is a pure function of those three)."""
    return (
        a.total == b.total
        and len(a.payments) == len(b.payments)
        and a.cadence == b.cadence
        and a.dates[0] == b.dates[0]
    )


def _schedule_breakdown(offer: negotiation.Offer) -> str:
    """Exact per-payment amount and date, for logs -- not the speech-shaped
    summary app/negotiation.py's `reason` text uses."""
    return ", ".join(
        f"${p} on {d.isoformat()}" for p, d in zip(offer.payments, offer.dates)
    )


# --- record_agreement: the single write, re-validated server-side ---
async def _persist_agreement(
    session: CallSession, offer: negotiation.Offer, discount_counters_issued: int, date_counters_issued: int
) -> None:
    """One write path for every agreement, single payment included: a
    payment_plans row always carries the schedule (so a deferred lump sum
    records a date somewhere queryable, not just accounts.status), plus a
    settlement close for a single payment. `amount_per_installment` is the
    average, not the first payment, so "N x $X" always equals the total
    even for an uneven split; `payments_breakdown` keeps the exact amounts
    alongside it. discount/date_counters_issued are stored at record time
    so the dashboard can distinguish accepting the opening offer from
    holding out on a discount or a date."""
    account_id = session.account_id
    average = (offer.total / len(offer.payments)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    breakdown = ",".join(str(p) for p in offer.payments)
    dates_breakdown = ",".join(d.isoformat() for d in offer.dates)
    await db.create_payment_plan(
        account_id, len(offer.payments), average, offer.total, offer.dates[0], breakdown,
        discount_counters_issued, date_counters_issued, session.session_id,
        offer.tier.value, dates_breakdown,
    )
    if len(offer.payments) == 1:
        await db.apply_settlement(account_id)
    await db.log_communication(
        account_id,
        f"Agreement recorded ({offer.tier.value}): ${offer.total} total across "
        f"{len(offer.payments)} payment(s): {_schedule_breakdown(offer)}.",
    )


async def _execute_record_agreement_tool_call(args: dict, session: CallSession) -> dict:
    async with session.agreement_lock:
        if session.agreement_recorded:
            logger.info("Agreement already recorded this call.")
            result = {"status": "already_recorded"}
            _log_tool_call(session, "record_agreement", args, result)
            return result

        proposal = _proposal_from_args(args)
        balance = Decimal(str(session.account_balance))
        call_date = session.call_started_at.date()

        # Re-validate against a *copy* of the negotiation state: a rejected
        # write must not spend the concession gate. If it mutated the live
        # state, a hallucinated or malformed record_agreement call would
        # silently unlock the next real discount request for free.
        # `offered` is explicitly re-copied -- dataclasses.replace() only
        # shallow-copies fields, so without this the copy and the live
        # state would share the same set object, and validate_proposal
        # mutating it in place (.add()) would leak into live state exactly
        # like the gate gap this comment warns about.
        state_for_check = dataclasses.replace(
            session.negotiation_state, offered=set(session.negotiation_state.offered)
        )
        verdict = negotiation.validate_proposal(balance, proposal, call_date, state_for_check)

        if verdict.decision != "ACCEPT":
            logger.warning("record_agreement refused: proposed terms did not validate.")
            result = {
                "status": "rejected",
                "reason": verdict.reason,
                "offer": _offer_to_dict(verdict.counter_offer) if verdict.counter_offer else None,
            }
            _log_tool_call(session, "record_agreement", args, result)
            return result

        offer = verdict.accepted_offer
        # record_agreement's wire schema carries only total/count/cadence/
        # first-date, so a re-validated ACCEPT always re-derives an even
        # split -- fine for a consumer who never proposed an uneven one,
        # but silently overwrites an agreed uneven split (e.g. "$600 today,
        # $400 in two weeks") with a normalised "$500/$500" nobody agreed
        # to. If the last ACCEPT this call (see
        # _execute_negotiate_tool_call) matches these same terms,
        # persist ITS exact payments instead. No match -- e.g. the model
        # skipped straight to record_agreement, or the customer's terms
        # changed since -- falls back to the re-derived even split exactly
        # as before; the server-side re-validation above is unaffected
        # either way.
        stored = session.accepted_offer
        if stored is not None and _offer_terms_match(stored, offer):
            offer = dataclasses.replace(offer, payments=stored.payments)

        await _persist_agreement(
            session, offer,
            session.negotiation_state.discount_counters_issued,
            session.negotiation_state.date_counters_issued,
        )

        session.agreement_recorded = True
        session.agreement_disposition = "SETTLED" if len(offer.payments) == 1 else "PAYMENT_PLAN_ACTIVE"
        append_call_log(
            session, "Billing",
            f"Agreement recorded: {offer.tier.value}, ${offer.total} total across "
            f"{len(offer.payments)} payment(s): {_schedule_breakdown(offer)}.",
        )

        result = {"status": "success", **_offer_to_dict(offer)}
        _log_tool_call(session, "record_agreement", args, result)
        return result


# --- Dispatch ---
_FUNCTION_CALL_HANDLERS = {
    "negotiate": _execute_negotiate_tool_call,
    "record_agreement": _execute_record_agreement_tool_call,
}


async def handle_function_call_request(message, session: CallSession) -> None:
    """Executes each client_side function Deepgram asked for and reports
    the result back over the same connection."""
    for function_call in message.functions:
        handler = _FUNCTION_CALL_HANDLERS.get(function_call.name)
        args = None
        if handler is None:
            # A response must still go back even for a name Deepgram sent
            # that this module doesn't recognise -- silently skipping it
            # (the previous behaviour) never replies, and the Voice Agent
            # then waits forever for a function-call response that is
            # never coming, stalling the call exactly like an unhandled
            # exception would if this weren't caught.
            logger.warning("Unknown function call request: %s", function_call.name)
            session.error_count += 1
            result = {
                "status": "error",
                "message": "That didn't go through due to a system issue -- let's try again.",
            }
            append_call_log(session, "Tool", f"{function_call.name}(unrecognized function)")
            append_call_log(session, "Tool", f"-> {_format_tool_result(result)}")
        else:
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
                append_call_log(
                    session, "Tool",
                    f"{function_call.name}({_format_tool_args(args) if args is not None else 'unparseable arguments'})",
                )
                append_call_log(session, "Tool", f"-> {_format_tool_result(result)}")

        await session.agent_connection.send_function_call_response(
            AgentV1SendFunctionCallResponse(
                id=function_call.id,
                name=function_call.name,
                content=json.dumps(result),
            )
        )
