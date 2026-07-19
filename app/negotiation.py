"""Deterministic negotiation validator for the debt-collection outcome ladder.

Pure Python: no LLM calls, no database access, no async, no network, no file
I/O, no reads of the system clock. `call_date` is always passed in by the
caller so results are reproducible. The agent never decides whether an
amount is acceptable and never invents a counter-offer -- it elicits a
proposal, calls `validate_proposal`, and speaks back whatever `Verdict` this
module returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
# Symmetric with the discount gate (see NegotiationState.date_unlocked): the
# 14-day first-payment cap is countered once, then held terms are accepted.
MAX_DATE_COUNTERS = 1

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
    decision: Literal["ACCEPT", "COUNTER", "NO_AGREEMENT"]
    reason: str  # plain language, safe for the agent to speak verbatim
    accepted_offer: Offer | None
    counter_offer: Offer | None
    violations: list[str]  # machine-readable codes, for logs only -- never spoken
    # Set only when `payment_below_floor` is among `violations` on this
    # verdict -- see the module docstring's "Surface the floor contextually".
    # None on every other verdict, deliberately: standing in context on
    # every response would let the model construct legal offers itself.
    minimum_payment: Decimal | None = None


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
    date_counters_issued: int = 0
    # The consumer's largest stated affordable single payment (most recent
    # statement wins). None means unknown -- no capacity filtering applies
    # yet, so selection falls back to tier order alone.
    capacity: Decimal | None = None
    # Every (tier, payment count, cadence) already offered as a counter this
    # call -- assumed refused if the consumer is still negotiating. Keyed on
    # the full triple, not just tier: refusing 3-monthly must not also mark
    # 3-biweekly and 3-weekly as refused.
    offered: set[tuple[Tier, int, Cadence]] = field(default_factory=set)
    # True once selection has fallen back to "cheapest thing that still
    # fits the window, offered once" (step 7 -- nothing is affordable).
    # Without this, successive refusals walk the entire candidate list in
    # ascending price order, offering progressively *more* expensive
    # arrangements to a consumer who couldn't afford the cheapest one --
    # see _select_counter.
    unreachable_offer_made: bool = False

    @property
    def discount_unlocked(self) -> bool:
        return self.discount_counters_issued >= MAX_DISCOUNT_COUNTERS

    @property
    def date_unlocked(self) -> bool:
        return self.date_counters_issued >= MAX_DATE_COUNTERS


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
        # 24 is a hard ceiling against date-arithmetic overflow only (STT
        # mishearing "3" as "100000" must still counter, not raise). The
        # ladder's real per-tier caps (TIER_RULES, much lower) are enforced
        # by _collect_violations/selection, not here -- "$200 a month for
        # five months" is a legal thing to say and must reach that path
        # rather than short-circuiting to degenerate_input.
        if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= 24):
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


def _is_clean(
    total: Decimal, n: int, cadence: Cadence, first_date: date, balance: Decimal, call_date: date,
    date_gate_unlocked: bool = False,
) -> bool:
    dates = _payment_dates(first_date, cadence, n)
    payments = _split_payments(total, n)
    tier = _classify_tier(total, n, balance)
    violations = _collect_violations(total, n, payments, dates, tier, balance, call_date)
    if date_gate_unlocked:
        violations = [v for v in violations if v != "first_payment_too_late"]
    return not violations


def _clamp_first_date(
    first_date: date, cadence: Cadence, n: int, call_date: date, enforce_delay_cap: bool = True
) -> date:
    """The tightest valid first-payment date: never before today, never so
    late that the *last* payment would miss the 3-calendar-month window
    (hard, always enforced), and -- while `enforce_delay_cap` is set --
    never later than the 14-day soft cap either. `enforce_delay_cap` is
    False once the date gate has unlocked (see NegotiationState.date_unlocked):
    a consumer who held their ground on a later date has earned it, so the
    dates-repair step must stop pulling it back, while the hard duration
    window still applies unconditionally."""
    duration_cap = _latest_first_date_for_duration(cadence, n, call_date)
    if not enforce_delay_cap:
        return max(call_date, min(first_date, duration_cap))
    delay_cap = call_date + timedelta(days=MAX_FIRST_PAYMENT_DELAY_DAYS)
    return max(call_date, min(first_date, delay_cap, duration_cap))


def _assert_clean(offer: Offer, balance: Decimal, call_date: date, date_gate_unlocked: bool = False) -> None:
    """Internal invariant: every counter-offer this module returns must
    pass its own validity checks (the soft 14-day cap excepted once the
    date gate has unlocked -- see _clamp_first_date)."""
    violations = _collect_violations(offer.total, len(offer.payments), offer.payments, offer.dates, offer.tier, balance, call_date)
    if date_gate_unlocked:
        violations = [v for v in violations if v != "first_payment_too_late"]
    assert not violations, f"internal counter-offer failed its own validation: {violations}"


def _repair_offer(
    total: Decimal, n: int, cadence: Cadence, first_date: date, balance: Decimal, call_date: date,
    date_gate_unlocked: bool = False,
) -> Offer:
    """Sequential, minimal repair: fix the smallest thing that's broken, in a
    fixed order (amount, count, split, dates), re-checking after each step
    and stopping as soon as the result is clean. Never regresses further
    than necessary -- a proposal broken only in its dates keeps its amount
    and count; only a proposal broken beyond repair falls back to the
    canonical opening offer. `date_gate_unlocked` makes the dates step (and
    the interim cleanliness checks) stop treating a held-onto late first
    date as broken -- see _clamp_first_date."""
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
    if _is_clean(total, n, cadence, first_date, balance, call_date, date_gate_unlocked):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 2: count -- cap to the (possibly-repaired) tier's max_payments.
    tier = _classify_tier(total, n, balance)
    max_n = TIER_RULES[tier].max_payments
    if n > max_n:
        n = max_n
    if _is_clean(total, n, cadence, first_date, balance, call_date, date_gate_unlocked):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 3: split -- _build_offer always uses an even split, so a floor
    # violation from an uneven proposal is already fixed by this point.
    if _is_clean(total, n, cadence, first_date, balance, call_date, date_gate_unlocked):
        return _build_offer(total, n, cadence, first_date, balance)

    # Step 4: dates -- pull the start only as far as needed to satisfy the
    # 3-calendar-month duration window (always), the 14-day soft cap (only
    # while the date gate is still locked), and never before today.
    first_date = _clamp_first_date(first_date, cadence, n, call_date, enforce_delay_cap=not date_gate_unlocked)
    if _is_clean(total, n, cadence, first_date, balance, call_date, date_gate_unlocked):
        return _build_offer(total, n, cadence, first_date, balance)

    # Safety net -- shouldn't be reachable given the rule set above.
    return opening_offer(balance, call_date)


# --- Candidate enumeration & capacity-scored selection ---
# The task's stated tier preference (never value order -- see the module
# docstring's Selection algorithm step 5): T3 collects less than T4 despite
# sorting earlier, because T3 is gated and only reachable once the consumer
# has demanded and held a discount. A discount is never conceded unprompted.
_TIER_ORDER: dict[Tier, int] = {
    Tier.FULL: 0,
    Tier.DOWNPAYMENT_PLUS_ONE: 1,
    Tier.SETTLEMENT: 2,
    Tier.PAYMENT_PLAN: 3,
}

# Cadence order is only a tie-break default -- it never affects legality,
# only which candidate wins when the consumer's own stated cadence doesn't
# match any multi-payment candidate (e.g. they proposed a lump sum:
# cadence "once"). Monthly first: the least frequent, most manageable
# spacing is the sensible default for a struggling consumer absent any
# stated preference.
_MULTI_PAYMENT_CADENCES = (Cadence.MONTHLY, Cadence.BIWEEKLY, Cadence.WEEKLY)

# T2's split is fixed by the floor ([balance - floor, floor], see
# _maximize_downpayment_split) regardless of cadence, so cadence only ever
# shifts the second payment's date -- it is not a genuine choice the way it
# is for T3/T4, where different cadences change the actual payment amounts.
# Enumerating all three produced three near-identical counters in a row
# (same $750/$250 split, same first payment, only the second date moving)
# before selection ever advanced to a materially different tier. One
# canonical cadence -- biweekly, the documented Tier 2 arrangement ($750
# today, $250 in two weeks) -- collapses that repetition without losing any
# reachable outcome: a consumer who wants a different second-payment date
# for the same split can still propose one directly, and it's accepted on
# its own merits by the normal proposal path (this only changes what the
# module *offers*, never what it *accepts* -- see the module docstring's
# "Consumer proposals still take precedence").
_T2_CADENCES = (Cadence.BIWEEKLY,)


def _candidate_specs(balance: Decimal) -> list[tuple[Tier, int, Cadence, Decimal]]:
    """Every legal arrangement on the ladder (tier, payment count, cadence,
    total), before window/capacity/offered filtering -- see the module
    docstring's candidate table. T3's total is fixed at the settlement
    ceiling (the deepest legal discount); T1/T2/T4 always collect the full
    balance and differ only in shape. T2 -- unlike T3/T4 -- yields exactly
    one candidate: see _T2_CADENCES."""
    settlement_total = _quantize(balance * TIER_RULES[Tier.SETTLEMENT].min_total_pct)
    specs = [(Tier.FULL, 1, Cadence.ONCE, balance)]
    specs += [(Tier.DOWNPAYMENT_PLUS_ONE, 2, c, balance) for c in _T2_CADENCES]
    specs.append((Tier.SETTLEMENT, 1, Cadence.ONCE, settlement_total))
    specs += [
        (Tier.SETTLEMENT, n, c, settlement_total) for n in (2, 3) for c in _MULTI_PAYMENT_CADENCES
    ]
    specs += [
        (Tier.PAYMENT_PLAN, n, c, balance) for n in (2, 3, 4) for c in _MULTI_PAYMENT_CADENCES
    ]
    return specs


def _candidate_payments(tier: Tier, n: int, total: Decimal, balance: Decimal) -> list[Decimal]:
    """T2's split is fixed by the floor (front-load as much as possible);
    every other tier splits evenly."""
    if tier == Tier.DOWNPAYMENT_PLUS_ONE:
        return _maximize_downpayment_split(total, _floor(balance))
    return _split_payments(total, n)


def _offer_key(offer: Offer) -> tuple[Tier, int, Cadence]:
    return (offer.tier, len(offer.payments), offer.cadence)


def _select_counter(
    balance: Decimal,
    call_date: date,
    state: NegotiationState,
    nominal_start: date,
    cadence_hint: Cadence,
    exclude_settlement: bool,
) -> Offer | None:
    """Picks the best reachable arrangement given what the consumer can
    afford, rather than minimally repairing whatever they proposed -- see
    the module docstring's Selection algorithm (this function is steps
    1-7). Returns None when every legal arrangement that still fits the
    window has already been offered and refused, or when nothing was ever
    affordable and the one-time "cheapest thing available" fallback (step
    7) has already been made -- the caller turns either case into a
    NO_AGREEMENT verdict."""
    # The anchor for both window-fit filtering and the returned offer's own
    # dates: never before today, and -- while the date gate is still locked
    # -- never later than the 14-day soft cap either. Deliberately NOT
    # clamped per-candidate against each candidate's own duration window;
    # a candidate that can't fit from this one shared start is discarded
    # (step 3), not silently reshaped to fit.
    start = max(call_date, nominal_start)
    if not state.date_unlocked:
        start = min(start, call_date + timedelta(days=MAX_FIRST_PAYMENT_DELAY_DAYS))
    boundary = _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS)

    candidates = []
    for tier, n, cadence, total in _candidate_specs(balance):
        if exclude_settlement and tier == Tier.SETTLEMENT:  # step 2
            continue
        if (tier, n, cadence) in state.offered:  # step 1
            continue
        dates = _payment_dates(start, cadence, n)
        if dates[-1] > boundary:  # step 3
            continue
        payments = _candidate_payments(tier, n, total, balance)
        candidates.append((tier, n, cadence, total, payments, dates))

    if not candidates:
        return None

    def largest_payment(c: tuple) -> Decimal:
        return max(c[4])

    # Step 4.
    reachable = [c for c in candidates if state.capacity is None or largest_payment(c) <= state.capacity]

    if reachable:
        # Step 5-6: a not-yet-tried (tier, n) combination first, then tier
        # order, then fewer payments, then the consumer's stated cadence.
        #
        # The freshness term exists because all three cadence variants of a
        # given (tier, n) sort adjacently under tier-order-then-n alone, so
        # a consumer who never mentioned timing would hear the same money
        # on three different schedules before selection ever moved to a
        # different tier or payment count -- e.g. "$300 today and $300 on
        # August 20th", then the same split again with only the second date
        # moved. Projecting cadence away from `state.offered` gives the set
        # of (tier, n) pairs already tried at ANY cadence; a candidate whose
        # (tier, n) is in that set, and whose cadence isn't the consumer's
        # own, is deprioritised behind anything still genuinely fresh --
        # but never excluded, so it's still selected once fresh
        # combinations run out (nothing becomes unreachable this way).
        #
        # `cadence != cadence_hint` is load-bearing: a candidate matching
        # the consumer's stated cadence must never be penalised as stale,
        # or a consumer who explicitly asks for weekly after being offered
        # monthly at the same (tier, n) would have that request outranked
        # by an unrelated fresh combination they never asked for.
        offered_tier_n = {(tier, n) for tier, n, _cadence in state.offered}

        def sort_key(c: tuple) -> tuple[int, int, int, int]:
            tier, n, cadence = c[0], c[1], c[2]
            stale = (tier, n) in offered_tier_n and cadence != cadence_hint
            return (1 if stale else 0, _TIER_ORDER[tier], n, 0 if cadence == cadence_hint else 1)

        chosen = min(reachable, key=sort_key)
    else:
        # Step 7: nothing is affordable -- offer the cheapest thing that
        # still fits the window, once per call. A second time through this
        # branch means that one-time offer was already made (and refused,
        # since it's now either accepted or back here again) -- there is
        # nothing honest left to escalate to.
        if state.unreachable_offer_made:
            return None
        state.unreachable_offer_made = True
        chosen = min(candidates, key=largest_payment)

    tier, n, cadence, total, payments, dates = chosen
    offer = Offer(tier=tier, total=total, payments=payments, dates=dates, cadence=cadence)
    _assert_clean(offer, balance, call_date, date_gate_unlocked=state.date_unlocked)
    return offer


def _no_agreement_verdict() -> Verdict:
    return Verdict(
        decision="NO_AGREEMENT",
        reason=(
            "I'm not able to put together an arrangement within what I can approve, so I'll "
            "pass this to one of our collectors to look at. The balance and these options stay "
            "open in the meantime."
        ),
        accepted_offer=None,
        counter_offer=None,
        violations=["no_agreement_possible"],
        minimum_payment=None,
    )


# --- Reason text (speech-shaped, spoken verbatim) ---
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_COUNT_WORDS = {2: "two", 3: "three", 4: "four"}


def _ordinal_day(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _speak_date(d: date) -> str:
    return f"{_MONTH_NAMES[d.month - 1]} {_ordinal_day(d.day)}"


def _speak_amount(amount: Decimal) -> str:
    """Drops a trailing .00 -- spoken by Deepgram's TTS as "point zero
    zero" otherwise. Genuine cents are left alone; TTS pronounces
    "$266.67" correctly on its own."""
    amount = _quantize(amount)
    if amount == amount.to_integral_value():
        return f"${int(amount)}"
    return f"${amount}"


def _describe_offer(offer: Offer) -> str:
    """A single or down-payment-plus-one offer states every figure exactly
    (at most two numbers). A longer schedule summarizes instead of reading
    every amount and date -- doing so was taking ~20 seconds and getting
    interrupted."""
    n = len(offer.payments)
    if n == 1:
        return f"{_speak_amount(offer.total)} paid in full on {_speak_date(offer.dates[0])}"
    if n == 2:
        return (
            f"{_speak_amount(offer.payments[0])} on {_speak_date(offer.dates[0])} and "
            f"{_speak_amount(offer.payments[1])} on {_speak_date(offer.dates[1])}"
        )
    average = (offer.total / n).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    count_word = _COUNT_WORDS.get(n, str(n))
    return (
        f"{count_word} {offer.cadence.value} payments of about ${average}, "
        f"starting {_speak_date(offer.dates[0])}"
    )


_OPENING_REASON = "I didn't quite catch that -- let's start with paying the full balance today."


def _accept_reason(offer: Offer) -> str:
    return f"That works -- {_describe_offer(offer)}."


def _counter_reason(offer: Offer, minimum_payment: Decimal | None = None) -> str:
    reason = f"I can't approve that, but I can offer {_describe_offer(offer)}."
    if minimum_payment is not None:
        reason += f" The minimum payment is {_speak_amount(minimum_payment)}."
    return reason


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

    # The consumer's numbers signal capacity; most recent statement wins,
    # whether or not this particular proposal turns out to be legal. An
    # explicit split states their largest single payment directly ($600 of
    # $600/$400 -- they can clearly manage $600 in one go); averaging that
    # across payments would under-read it and silently filter out
    # candidates they could in fact afford. Only a bare total/count (no
    # split given) falls back to the average.
    state.capacity = (
        _quantize(max(proposal.payments)) if proposal.payments is not None else _quantize(total / n)
    )

    tier = _classify_tier(total, n, balance)

    # Checked before violations/repair: any discount request while the gate
    # is locked gets the no-discount ladder, regardless of what else is
    # wrong with the proposal. T3 is dropped from the candidate pool
    # entirely rather than repaired up to the ceiling with the consumer's
    # own shape preserved -- see the module docstring for the bug this
    # replaces (capacity $200 being countered with "$1,000 in one payment").
    if tier == Tier.SETTLEMENT and not state.discount_unlocked:
        # The gate is spent only if excluding T3 actually changed the
        # outcome -- e.g. capacity $200 can't reach any settlement
        # candidate either, so dropping T3 buys nothing and nothing should
        # be withheld from the consumer's single counter. Probed against
        # an independent copy of `state` (offered re-copied -- replace()
        # only shallow-copies, and _select_counter mutates `offered` and
        # `unreachable_offer_made` in place) so the comparison itself has
        # no effect on the live negotiation; only the real, returned
        # counter (`without_t3`, computed against the live state) may
        # mutate it, and exactly once.
        probe_state = replace(state, offered=set(state.offered))
        with_t3 = _select_counter(
            balance, call_date, probe_state, nominal_start=first_date, cadence_hint=cadence,
            exclude_settlement=False,
        )
        without_t3 = _select_counter(
            balance, call_date, state, nominal_start=first_date, cadence_hint=cadence,
            exclude_settlement=True,
        )
        if without_t3 is None:
            return _no_agreement_verdict()
        if with_t3 is not None and _offer_key(with_t3) != _offer_key(without_t3):
            state.discount_counters_issued += 1
        state.offered.add(_offer_key(without_t3))

        # Floor context (section 8): the proposal's own payments, not the
        # counter's -- surfaced whenever what the consumer asked for would
        # have breached the floor, regardless of whether the gate was
        # actually spent.
        proposed_payments = proposal.payments if proposal.payments is not None else _split_payments(total, n)
        minimum_payment = _floor(balance) if any(p < _floor(balance) for p in proposed_payments) else None

        return Verdict(
            decision="COUNTER",
            reason=_counter_reason(without_t3, minimum_payment),
            accepted_offer=None,
            counter_offer=without_t3,
            violations=["discount_gate_locked"],
            minimum_payment=minimum_payment,
        )

    dates = _payment_dates(first_date, cadence, n)
    payments = proposal.payments if proposal.payments is not None else _split_payments(total, n)

    raw_violations = _collect_violations(total, n, payments, dates, tier, balance, call_date)

    # The 14-day soft cap gets the same counter-once-then-accept treatment
    # as the discount gate (see module docstring section 6): the first time
    # it fires this call, it still counts as a violation (forcing a
    # COUNTER this turn) even though the gate is now unlocked for every
    # later call.
    date_was_unlocked = state.date_unlocked
    if "first_payment_too_late" in raw_violations and not date_was_unlocked:
        state.date_counters_issued += 1

    violations = (
        [v for v in raw_violations if v != "first_payment_too_late"] if date_was_unlocked else raw_violations
    )

    if not violations:
        # Legal proposal: accept exactly as proposed, preserving the
        # consumer's own split, cadence, and dates -- never normalised to
        # a canonical candidate.
        accepted = Offer(tier=tier, total=total, payments=payments, dates=dates, cadence=cadence)
        return Verdict(
            decision="ACCEPT",
            reason=_accept_reason(accepted),
            accepted_offer=accepted,
            counter_offer=None,
            violations=[],
        )

    # Illegal proposal at a tier the consumer has already reached (T3 only
    # reaches this point once its gate is unlocked -- see above): try the
    # old minimal in-tier repair first. Only fall back to full selection if
    # that repair reproduces something already offered and refused.
    repaired = _repair_offer(total, n, cadence, first_date, balance, call_date, date_gate_unlocked=date_was_unlocked)
    if _offer_key(repaired) not in state.offered:
        offer = repaired
    else:
        offer = _select_counter(
            balance, call_date, state,
            nominal_start=first_date, cadence_hint=cadence,
            exclude_settlement=not state.discount_unlocked,
        )

    if offer is None:
        return _no_agreement_verdict()

    state.offered.add(_offer_key(offer))
    minimum_payment = _floor(balance) if "payment_below_floor" in violations else None
    return Verdict(
        decision="COUNTER",
        reason=_counter_reason(offer, minimum_payment),
        accepted_offer=None,
        counter_offer=offer,
        violations=violations,
        minimum_payment=minimum_payment,
    )
