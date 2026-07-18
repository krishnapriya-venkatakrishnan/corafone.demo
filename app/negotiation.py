"""Deterministic negotiation validator for the debt-collection outcome ladder.

Pure Python: no LLM calls, no database access, no async, no network, no file
I/O, no reads of the system clock. `call_date` is always passed in by the
caller so results are reproducible. The agent never decides whether an
amount is acceptable and never invents a counter-offer -- it elicits a
proposal, calls `validate_proposal`, and speaks back whatever `Verdict` this
module returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal

import calendar

# --- Configuration ---
# The 25% payment floor is ambiguous only on the settlement tier (every
# other tier is full-balance, so 25% of the total and 25% of the original
# balance are the same number). Chosen: 25% of the original balance, the
# stricter reading -- see `_floor` and
# test_ambiguity_case_counters_under_chosen_reading.
MIN_PAYMENT_PCT = Decimal("0.25")
MAX_SETTLEMENT_DISCOUNT_PCT = Decimal("0.20")
MAX_PLAN_DURATION_MONTHS = 3
MAX_FIRST_PAYMENT_DELAY_DAYS = 14
MAX_DISCOUNT_COUNTERS = 1

_CENTS = Decimal("0.01")


# --- Data model ---
class Cadence(str, Enum):
    ONCE = "once"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class Tier(str, Enum):
    FULL = "full_payment"
    DOWNPAYMENT_PLUS_ONE = "downpayment_plus_one"
    SETTLEMENT = "settlement"
    PAYMENT_PLAN = "payment_plan"


@dataclass(frozen=True)
class Offer:
    tier: Tier
    total: Decimal
    payments: list[Decimal]  # sums exactly to total
    dates: list[date]  # parallel to payments
    cadence: Cadence


@dataclass(frozen=True)
class Proposal:
    total: Decimal
    number_of_payments: int
    cadence: Cadence
    first_payment_date: date
    # None -- consumer stated only a total and a count, no specific split;
    # the validator proposes an even one. A provided list is the consumer's
    # own uneven split and, if valid, is preserved verbatim on ACCEPT rather
    # than silently replaced with an even one.
    payments: list[Decimal] | None = None


@dataclass(frozen=True)
class Verdict:
    decision: Literal["ACCEPT", "COUNTER"]
    reason: str  # plain language, safe for the agent to speak verbatim
    accepted_offer: Offer | None
    counter_offer: Offer | None
    violations: list[str]  # machine-readable codes, for logs only -- never spoken


@dataclass
class NegotiationState:
    """Mutable, caller-owned negotiation state for one call.

    The caller owns this object and must not invoke `validate_proposal` more
    than once per conversational turn for the same proposal. This module has
    no notion of conversational turns and cannot distinguish a duplicate
    tool call within one turn from a consumer genuinely repeating their
    number in a later turn -- that distinction belongs to whatever layer can
    see turn boundaries (e.g. app/voice_agent.py), which should memoize the
    verdict per turn rather than re-invoking validation.
    """

    discount_counters_issued: int = 0

    @property
    def discount_unlocked(self) -> bool:
        return self.discount_counters_issued >= MAX_DISCOUNT_COUNTERS


@dataclass(frozen=True)
class TierRule:
    min_total_pct: Decimal
    max_payments: int


TIER_RULES: dict[Tier, TierRule] = {
    Tier.FULL: TierRule(Decimal("1.00"), 1),
    Tier.DOWNPAYMENT_PLUS_ONE: TierRule(Decimal("1.00"), 2),
    Tier.SETTLEMENT: TierRule(Decimal("1") - MAX_SETTLEMENT_DISCOUNT_PCT, 3),
    Tier.PAYMENT_PLAN: TierRule(Decimal("1.00"), 4),
}

_CADENCE_STEP_DAYS: dict[Cadence, int] = {
    Cadence.WEEKLY: 7,
    Cadence.BIWEEKLY: 14,
}


# --- Money handling ---
def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _split_payments(total: Decimal, n: int) -> list[Decimal]:
    """Splits `total` into `n` payments summing exactly to `total`, with the
    rounding remainder distributed one cent at a time across the first
    payments -- e.g. $1,000/3 is 333.34/333.33/333.33, $800/3 is
    266.67/266.67/266.66. No two payments ever differ by more than a cent,
    which the floor check relies on: an even split of a legal total/count
    must always clear the floor."""
    total = _quantize(total)
    if n == 1:
        return [total]
    cents = int(total * 100)
    base, remainder = divmod(cents, n)
    amounts = [base + 1] * remainder + [base] * (n - remainder)
    return [_quantize(Decimal(amount) / 100) for amount in amounts]


def _maximize_downpayment_split(total: Decimal, floor: Decimal) -> list[Decimal]:
    """The tier-2 convention: front-load as much as possible while leaving
    the remaining payment(s) at exactly the floor."""
    total = _quantize(total)
    floor = _quantize(floor)
    return [_quantize(total - floor), floor]


# --- Calendar-month arithmetic (no python-dateutil dependency) ---
def _add_calendar_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _payment_dates(first_payment_date: date, cadence: Cadence, n: int) -> list[date]:
    if n <= 1:
        return [first_payment_date]
    if cadence == Cadence.MONTHLY:
        return [_add_calendar_months(first_payment_date, i) for i in range(n)]
    step = _CADENCE_STEP_DAYS.get(cadence, 0)
    return [first_payment_date + timedelta(days=step * i) for i in range(n)]


def _latest_first_date_for_duration(cadence: Cadence, n: int, call_date: date) -> date:
    """The latest first-payment date such that, at this cadence and count,
    the *last* payment still lands within MAX_PLAN_DURATION_MONTHS."""
    boundary = _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS)
    if n <= 1:
        return boundary
    if cadence == Cadence.MONTHLY:
        return _add_calendar_months(boundary, -(n - 1))
    step = _CADENCE_STEP_DAYS.get(cadence, 0)
    return boundary - timedelta(days=step * (n - 1))


# --- Tier classification & floor basis ---
def _classify_tier(total: Decimal, n: int, balance: Decimal) -> Tier:
    """Discounted totals are always the settlement tier -- it's the only
    tier that permits less than the full balance. Full-balance proposals are
    classified by payment count; FULL/DOWNPAYMENT_PLUS_ONE/PAYMENT_PLAN share
    the same 100% floor and differ only in payment cap and which canonical
    counter-offer convention applies to them."""
    if total < balance:
        return Tier.SETTLEMENT
    if n == 1:
        return Tier.FULL
    if n == 2:
        return Tier.DOWNPAYMENT_PLUS_ONE
    return Tier.PAYMENT_PLAN


def _floor(balance: Decimal) -> Decimal:
    # Rounded down, not to the nearest cent: an even split (_split_payments)
    # always distributes at least this much to every payment, so the floor
    # must round in the direction a split can always reach, not away from it.
    return (MIN_PAYMENT_PCT * balance).quantize(_CENTS, rounding=ROUND_DOWN)


# --- Sanity guard ---
def _is_sane(proposal: Proposal) -> bool:
    """Never raises. Treats every field as hostile -- speech-to-text mangles
    numbers and dates routinely, and this is the last thing standing between
    a mis-transcription and a database write."""
    try:
        if not isinstance(proposal.total, Decimal) or proposal.total <= 0:
            return False
        n = proposal.number_of_payments
        # 4 is the ladder's own outer bound -- a hard limit, not just a
        # repair target, since an unbounded n reaches date arithmetic
        # before any repair step runs and can overflow it.
        if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= 4):
            return False
        if not isinstance(proposal.cadence, Cadence):
            return False
        if proposal.cadence == Cadence.ONCE and n != 1:
            return False
        if not isinstance(proposal.first_payment_date, date):
            return False
        if proposal.payments is not None:
            if len(proposal.payments) != n:
                return False
            if any(not isinstance(p, Decimal) or p <= 0 for p in proposal.payments):
                return False
            if _quantize(sum(proposal.payments)) != _quantize(proposal.total):
                return False
        return True
    except (TypeError, ValueError, ArithmeticError):
        return False


# --- Validity checks (everything except the concession gate) ---
def _collect_violations(
    total: Decimal,
    n: int,
    payments: list[Decimal],
    dates: list[date],
    tier: Tier,
    balance: Decimal,
    call_date: date,
) -> list[str]:
    violations = []
    rule = TIER_RULES[tier]

    if total < _quantize(balance * rule.min_total_pct):
        violations.append("discount_too_deep")

    if total > balance:
        violations.append("overpayment")

    floor = _floor(balance)
    if any(p < floor for p in payments):
        violations.append("payment_below_floor")

    if n > rule.max_payments:
        violations.append("too_many_payments")

    if dates and dates[-1] > _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS):
        violations.append("duration_exceeds_window")

    if dates and (dates[0] - call_date).days > MAX_FIRST_PAYMENT_DELAY_DAYS:
        violations.append("first_payment_too_late")

    if dates and dates[0] < call_date:
        violations.append("first_payment_in_past")

    return violations


def _gate_locks_discount(total: Decimal, balance: Decimal, state: NegotiationState) -> bool:
    """True if this proposal is a discount request and the concession gate
    hasn't been spent yet. Keyed on the proposal's own total, checked before
    any repair runs -- an illegal split on a discount request must still
    hit the no-discount ladder, not get repaired up to the maximum legal
    discount instead. Counter-keyed, not identity-keyed -- see
    NegotiationState's docstring for why."""
    return total < balance and not state.discount_unlocked


# --- Counter-offer generation ---
def opening_offer(balance: Decimal, call_date: date) -> Offer:
    balance = _quantize(balance)
    return Offer(tier=Tier.FULL, total=balance, payments=[balance], dates=[call_date], cadence=Cadence.ONCE)


def _build_offer(total: Decimal, n: int, cadence: Cadence, first_date: date, balance: Decimal) -> Offer:
    tier = _classify_tier(total, n, balance)
    return Offer(
        tier=tier,
        total=total,
        payments=_split_payments(total, n),
        dates=_payment_dates(first_date, cadence, n),
        cadence=cadence,
    )


def _is_clean(total: Decimal, n: int, cadence: Cadence, first_date: date, balance: Decimal, call_date: date) -> bool:
    dates = _payment_dates(first_date, cadence, n)
    payments = _split_payments(total, n)
    tier = _classify_tier(total, n, balance)
    return not _collect_violations(total, n, payments, dates, tier, balance, call_date)


def _clamp_first_date(first_date: date, cadence: Cadence, n: int, call_date: date) -> date:
    """The tightest valid first-payment date: never before today, never later
    than the 14-day soft cap, and never so late that the *last* payment
    would miss the 3-calendar-month window. Shared by the dates-repair step
    and the discount-gate counter so both get this guarantee the same way."""
    delay_cap = call_date + timedelta(days=MAX_FIRST_PAYMENT_DELAY_DAYS)
    duration_cap = _latest_first_date_for_duration(cadence, n, call_date)
    return max(call_date, min(first_date, delay_cap, duration_cap))


def _assert_clean(offer: Offer, balance: Decimal, call_date: date) -> None:
    """Internal invariant: every counter-offer this module returns must
    pass its own validity checks."""
    violations = _collect_violations(offer.total, len(offer.payments), offer.payments, offer.dates, offer.tier, balance, call_date)
    assert not violations, f"internal counter-offer failed its own validation: {violations}"


def _repair_offer(
    total: Decimal, n: int, cadence: Cadence, first_date: date, balance: Decimal, call_date: date
) -> Offer:
    """Sequential, minimal repair: fix the smallest thing that's broken, in a
    fixed order (amount, count, split, dates), re-checking after each step
    and stopping as soon as the result is clean. Never regresses further
    than necessary -- a proposal broken only in its dates keeps its amount
    and count; only a proposal broken beyond repair falls back to the
    canonical opening offer."""
    # Step 1: amount -- raise a too-deep discount to the settlement ceiling,
    # or cap an overpayment down to the balance (can't collect more than is
    # owed).
    tier = _classify_tier(total, n, balance)
    if tier == Tier.SETTLEMENT:
        ceiling = _quantize(balance * TIER_RULES[Tier.SETTLEMENT].min_total_pct)
        if total < ceiling:
            total = ceiling
    if total > balance:
        total = balance
    if _is_clean(total, n, cadence, first_date, balance, call_date):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 2: count -- cap to the (possibly-repaired) tier's max_payments.
    tier = _classify_tier(total, n, balance)
    max_n = TIER_RULES[tier].max_payments
    if n > max_n:
        n = max_n
    if _is_clean(total, n, cadence, first_date, balance, call_date):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 3: split -- _build_offer always uses an even split, so a floor
    # violation from an uneven proposal is already fixed by this point.
    if _is_clean(total, n, cadence, first_date, balance, call_date):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 4: dates -- pull the start only as far as needed to satisfy the
    # 14-day soft cap, the 3-calendar-month duration window, and never
    # before today.
    first_date = _clamp_first_date(first_date, cadence, n, call_date)
    if _is_clean(total, n, cadence, first_date, balance, call_date):
        return _build_offer(total, n, cadence, first_date, balance)

    # Safety net -- shouldn't be reachable given the rule set above.
    return opening_offer(balance, call_date)


def _discount_gate_counter(n: int, cadence: Cadence, first_date: date, balance: Decimal, call_date: date) -> Offer:
    """The no-discount counter for a locked discount request: preserves the
    consumer's own payment count and cadence/spacing (they've signalled what
    they can manage), and only replaces the amounts. Dates are clamped the
    same way a repair would be -- this counter is generated independently
    of the repair pipeline, so it has to guarantee its own validity rather
    than relying on the caller's dates happening to already be in range."""
    balance = _quantize(balance)
    first_date = _clamp_first_date(first_date, cadence, n, call_date)
    dates = _payment_dates(first_date, cadence, n)
    if n == 1:
        payments = [balance]
        tier = Tier.FULL
    elif n == 2:
        payments = _maximize_downpayment_split(balance, _floor(balance))
        tier = Tier.DOWNPAYMENT_PLUS_ONE
    else:
        payments = _split_payments(balance, n)
        tier = Tier.PAYMENT_PLAN
    offer = Offer(tier=tier, total=balance, payments=payments, dates=dates, cadence=cadence)
    _assert_clean(offer, balance, call_date)
    return offer


# --- Reason text (plain language, spoken verbatim) ---
def _describe_offer(offer: Offer) -> str:
    if len(offer.payments) == 1:
        return f"${offer.total} paid in full on {offer.dates[0].isoformat()}"
    amounts = ", ".join(f"${p}" for p in offer.payments)
    return (
        f"${offer.total} total across {len(offer.payments)} payments "
        f"({amounts}), starting {offer.dates[0].isoformat()}"
    )


_OPENING_REASON = "I didn't quite catch that -- let's start with paying the full balance today."


def _accept_reason(offer: Offer) -> str:
    return f"That works -- {_describe_offer(offer)}."


def _counter_reason(offer: Offer) -> str:
    return f"I can't approve that, but I can offer {_describe_offer(offer)}."


# --- Public API ---
def validate_proposal(balance: Decimal, proposal: Proposal, call_date: date, state: NegotiationState) -> Verdict:
    balance = _quantize(balance)

    if not _is_sane(proposal):
        offer = opening_offer(balance, call_date)
        return Verdict(
            decision="COUNTER",
            reason=_OPENING_REASON,
            accepted_offer=None,
            counter_offer=offer,
            violations=["degenerate_input"],
        )

    total = _quantize(proposal.total)
    n = proposal.number_of_payments
    cadence = proposal.cadence
    first_date = proposal.first_payment_date

    # Checked before violations/repair: any discount request while the gate
    # is locked gets the no-discount ladder, regardless of what else is
    # wrong with the proposal.
    if _gate_locks_discount(total, balance, state):
        state.discount_counters_issued += 1
        offer = _discount_gate_counter(n, cadence, first_date, balance, call_date)
        return Verdict(
            decision="COUNTER",
            reason=_counter_reason(offer),
            accepted_offer=None,
            counter_offer=offer,
            violations=["discount_gate_locked"],
        )

    dates = _payment_dates(first_date, cadence, n)
    payments = proposal.payments if proposal.payments is not None else _split_payments(total, n)
    tier = _classify_tier(total, n, balance)

    violations = _collect_violations(total, n, payments, dates, tier, balance, call_date)
    if violations:
        offer = _repair_offer(total, n, cadence, first_date, balance, call_date)
        return Verdict(
            decision="COUNTER",
            reason=_counter_reason(offer),
            accepted_offer=None,
            counter_offer=offer,
            violations=violations,
        )

    accepted = Offer(tier=tier, total=total, payments=payments, dates=dates, cadence=cadence)
    return Verdict(
        decision="ACCEPT",
        reason=_accept_reason(accepted),
        accepted_offer=accepted,
        counter_offer=None,
        violations=[],
    )
