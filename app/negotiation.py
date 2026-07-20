"""Deterministic negotiation validator for the debt-collection outcome ladder.

Pure Python: no LLM calls, no database access, no async, no network, no file
I/O, no reads of the system clock. `call_date` is always passed in by the
caller so results are reproducible. The agent never decides whether an
amount is acceptable and never invents a counter-offer -- it elicits
whatever the consumer said and speaks back whatever `Verdict` this module
returns.

The agent-facing tool is `negotiate` -- a single read-only entry point
(the model could not reliably choose between two separate tools; see its
docstring) that resolves whichever arguments are present into a call to
one of this module's two internal functions: `validate_proposal`, when
there's a proposal (a total and/or a specific split) to check, or
`request_next_offer`, the read-only counterpart that runs the same
capacity-scored selection when the consumer named no figure at all -- a
deflection, "I don't know", silence -- or only a bare capacity figure with
nothing else to resolve into a full proposal. Both remain independently
callable and independently tested; `negotiate` only decides which one
applies, never what's acceptable. Every `Verdict` also carries an
`agent_note` (machine-readable, never spoken -- see Verdict's docstring)
so a malformed tool call can be corrected instead of repeated verbatim.
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
# How many equal steps negotiate()'s discount_requested path walks from a
# small discount up to MAX_SETTLEMENT_DISCOUNT_PCT once the gate is
# unlocked -- see NegotiationState.settlement_steps_offered and
# negotiate()'s docstring. 4 steps at today's 20% ceiling means 5%, 10%,
# 15%, 20% -- "up to 20% off" read as a range the customer's own
# persistence walks through, not a number handed out in full the instant
# the gate opens.
SETTLEMENT_STEP_COUNT = 4

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


# The full-balance-in-one-payment opening anchor -- rule 3 mandates this be
# spoken directly on the turn after the Mini-Miranda disclosure, never
# through a tool call, so it can never land in `state.offered` on its own.
# Excluded unconditionally by _select_counter itself (see that function's
# own docstring), not by seeding it into NegotiationState.offered's default
# -- every real call has this spoken before `negotiate` is ever called, so
# treating it as "not yet offered" is never actually true, but seeding
# `offered` directly also wrongly blocked a customer's own LIVE proposal
# from repairing back into this same shape. Without the exclusion (in
# either form), once negotiation has gone several rounds deep and every
# other candidate is genuinely exhausted, selection could walk backward to
# it as if it were still fresh -- offering the full balance again, after
# two or more rounds of the customer refusing exactly that.
_OPENING_ANCHOR_KEY = (Tier.FULL, 1, Cadence.ONCE)

# The instruction the gate-locked path (validate_proposal's T3 branch and
# negotiate()'s own discount_requested-locked branch, below) gives the
# agent -- an instruction to call this tool again under a specific
# condition, never an explanation of how the gate works. See Verdict.
# agent_note for why that distinction matters and the live failure it
# guards against: told the RULE ("a settlement is not available until the
# customer has held their position once"), the model reasoned from it
# directly on the next turn instead of calling `negotiate` again to find
# out the gate had actually opened -- it never saw the unlocked result
# because it never made the call. Told an INSTRUCTION instead, there is
# nothing to reason from except "call the tool."
_GATE_LOCKED_AGENT_NOTE = "if the customer repeats or holds their request, call negotiate again"


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
    # Set only when the floor was actually breached by something the
    # consumer said -- either `payment_below_floor` is among `violations`
    # on this verdict (a live proposal's own payments), or (request_next_
    # offer only) a stated `customer_capacity` fell below it. None on every
    # other verdict, deliberately: standing in context on every response
    # would let the model construct legal offers itself.
    minimum_payment: Decimal | None = None
    # Machine-readable guidance for the AGENT, never the customer -- the
    # second audience `reason` used to serve badly. Restricted to two
    # things, and nothing else: (1) a malformed or illegal call to correct
    # (_sanity_violation, _explain_violations) -- these describe what was
    # wrong with THIS call so the next one can be built correctly; (2) a
    # forward-pointing INSTRUCTION for when to call this tool again (see
    # _GATE_LOCKED_AGENT_NOTE) -- never an explanation of how the gate or
    # any other internal rule works. A live call showed why that distinction
    # is load-bearing: told "a settlement is not available until the
    # customer has held their position once," the model treated it as a
    # fact to reason from rather than a call to make -- when the customer
    # held their position on the very next turn, it restated the prior
    # offer from memory instead of calling `negotiate` again to check
    # whether the gate had actually opened. Anything explanatory in this
    # field becomes something the model reasons from instead of deferring
    # to; an instruction keeps pointing back at the tool. None otherwise --
    # including on an ordinary, nothing-wrong-here COUNTER and on
    # NO_AGREEMENT (_no_agreement_verdict) -- `reason` alone is spoken and
    # complete there, and there is nothing left to correct or call again
    # for. A note on the routine path teaches the model to skim the field,
    # including on the call where it actually matters.
    agent_note: str | None = None
    # Structured, already-speech-formatted form of whichever offer this
    # verdict carries (accepted_offer or counter_offer) -- see
    # _offer_summary. Lets the agent build a confirmation turn (rule 5)
    # from data instead of reformatting the prose `reason`, which is where
    # a trailing ".00" or an expanded date list creeps back in. None only
    # when there's no offer at all (NO_AGREEMENT).
    offer_summary: dict | None = None


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
    # 3-biweekly and 3-weekly as refused. Deliberately does NOT seed
    # _OPENING_ANCHOR_KEY (the full-balance opening anchor rule 3 requires
    # spoken directly, never through a tool call) -- _select_counter
    # excludes it from every NEW counter it selects unconditionally, not
    # via this set, specifically so it stays excluded from fresh
    # *counter-offer selection* without also blocking the customer's own
    # LIVE proposal from repairing back into that exact shape (e.g. "pay
    # in full today" with a bad date should still minimally repair to
    # just the date, not get diverted into an unrelated tier because the
    # shape LOOKS like something already offered).
    offered: set[tuple[Tier, int, Cadence]] = field(default_factory=set)
    # True once selection has fallen back to "cheapest thing that still
    # fits the window, offered once" (step 7 -- nothing is affordable).
    # Without this, successive refusals walk the entire candidate list in
    # ascending price order, offering progressively *more* expensive
    # arrangements to a consumer who couldn't afford the cheapest one --
    # see _select_counter.
    unreachable_offer_made: bool = False
    # How many intermediate discount steps have been offered via
    # negotiate()'s discount_requested path (0 = none yet) -- see
    # negotiate()'s docstring for why these steps exist: the concession
    # gate controls WHEN a discount becomes available, not HOW MUCH: a
    # customer who names an actual figure is capped by their own number,
    # but a customer who names nothing was (before this field existed)
    # handed the maximum discount the instant the gate opened, however
    # small a reduction they might have accepted. Capped at
    # SETTLEMENT_STEP_COUNT; deliberately NOT reflected in `offered`
    # (every step shares the same (SETTLEMENT, 1, ONCE) key, so `offered`
    # cannot distinguish them, and adding any one of them would make
    # _select_counter treat the whole tier/count/cadence as exhausted for
    # every OTHER caller, including a customer's own later specific
    # proposal).
    settlement_steps_offered: int = 0

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
    the *last* payment still lands within MAX_PLAN_DURATION_MONTHS.

    The window is exclusive: a payment landing exactly on the
    three-calendar-month anniversary of `call_date` is outside it (see
    _collect_violations/_select_counter's matching `>=` checks -- all three
    sites must move together, see the module docstring). `boundary` here is
    therefore the anniversary minus one day, the latest date that still
    satisfies a strict `<` against the anniversary itself."""
    boundary = _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS) - timedelta(days=1)
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


def _settlement_step_total(balance: Decimal, step: int) -> Decimal:
    """The total for the Nth intermediate discount step (1-indexed,
    ascending discount depth -- step 1 is the shallowest discount, step
    SETTLEMENT_STEP_COUNT is exactly the ceiling). See
    NegotiationState.settlement_steps_offered and negotiate()'s
    docstring: negotiate()'s discount_requested path walks these in order
    rather than handing out the maximum discount the instant the gate
    opens."""
    pct = MAX_SETTLEMENT_DISCOUNT_PCT * step / SETTLEMENT_STEP_COUNT
    return _quantize(balance * (Decimal("1") - pct))


# --- Sanity guard ---
def _sanity_violation(proposal: Proposal) -> str | None:
    """Same checks as `_is_sane`, same order, same conditions -- this
    function IS the sanity guard; `_is_sane` is a thin bool wrapper over it
    so the two can never drift apart. Returns None when the proposal is
    sane, otherwise a plain-language explanation of exactly which
    constraint failed, for Verdict.agent_note -- the agent's only way to
    learn what was wrong with a malformed call instead of repeating it
    verbatim (see the module docstring). Never raises, for the same reason
    the old _is_sane didn't: every field is hostile input -- speech-to-text
    mangles numbers and dates routinely, and this is the last thing
    standing between a mis-transcription and a database write."""
    try:
        if not isinstance(proposal.total, Decimal) or proposal.total <= 0:
            return "total_amount must be a positive number"
        n = proposal.number_of_payments
        # 24 is a hard ceiling against date-arithmetic overflow only (STT
        # mishearing "3" as "100000" must still counter, not raise). The
        # ladder's real per-tier caps (TIER_RULES, much lower) are enforced
        # by _collect_violations/selection, not here -- "$200 a month for
        # five months" is a legal thing to say and must reach that path
        # rather than short-circuiting to degenerate_input.
        if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= 24):
            return "number_of_payments must be a whole number between 1 and 24"
        if not isinstance(proposal.cadence, Cadence):
            return "cadence must be one of 'once', 'weekly', 'biweekly', 'monthly'"
        if proposal.cadence == Cadence.ONCE and n != 1:
            return f"cadence 'once' requires exactly one payment, but number_of_payments was {n}"
        if not isinstance(proposal.first_payment_date, date):
            return "first_payment_date must be a valid date"
        if proposal.payments is not None:
            if len(proposal.payments) != n:
                return f"payments has {len(proposal.payments)} entries but number_of_payments was {n}"
            if any(not isinstance(p, Decimal) or p <= 0 for p in proposal.payments):
                return "every entry in payments must be a positive number"
            if _quantize(sum(proposal.payments)) != _quantize(proposal.total):
                return (
                    f"payments sum to {_speak_amount(sum(proposal.payments))} but total_amount was "
                    f"{_speak_amount(proposal.total)} -- they must match"
                )
        return None
    except (TypeError, ValueError, ArithmeticError):
        return "one or more fields could not be understood"


def _is_sane(proposal: Proposal) -> bool:
    return _sanity_violation(proposal) is None


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

    # Exclusive: a payment landing exactly on the three-calendar-month
    # anniversary of call_date is outside the window, not on its edge --
    # see _latest_first_date_for_duration and _select_counter's matching
    # `>=` check. All three sites must move together.
    if dates and dates[-1] >= _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS):
        violations.append("duration_exceeds_window")

    if dates and (dates[0] - call_date).days > MAX_FIRST_PAYMENT_DELAY_DAYS:
        violations.append("first_payment_too_late")

    if dates and dates[0] < call_date:
        violations.append("first_payment_in_past")

    return violations


# Plain-language explanations of _collect_violations' machine-readable
# codes, for Verdict.agent_note on the ordinary repair/select COUNTER path
# -- the codes themselves already exist for logs; this is the same
# information, worded for the agent to read rather than grep.
_VIOLATION_EXPLANATIONS: dict[str, str] = {
    "discount_too_deep": "the proposed total is below the legal settlement floor (80% of the balance)",
    "overpayment": "the proposed total exceeds the account balance",
    "payment_below_floor": "at least one payment falls below the minimum payment floor",
    "too_many_payments": "the proposal uses more payments than this tier allows",
    "duration_exceeds_window": "the last payment would land outside the 3-month window",
    "first_payment_too_late": "the first payment date is later than the 14-day soft cap",
    "first_payment_in_past": "the first payment date is before today",
}


def _explain_violations(violations: list[str]) -> str:
    explanations = [_VIOLATION_EXPLANATIONS[v] for v in violations if v in _VIOLATION_EXPLANATIONS]
    return "; ".join(explanations) if explanations else "the proposal did not validate as given"


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
# Tie-break only, not the primary order -- selection ranks by total collected
# (descending) first (see _select_counter's sort_key), so tier order here
# only decides between candidates that already collect the same amount (e.g.
# FULL vs. DOWNPAYMENT_PLUS_ONE vs. PAYMENT_PLAN, all full-balance). Before
# this, a fully-affordable payment plan collecting the whole balance lost to
# a settlement purely because T3 sorted earlier in this table -- a discount
# was conceded to a consumer who could afford everything, which is not what
# "up to 20% off" means. A settlement is a concession for affordability or
# for a figure the customer actually named -- never a default just because
# the gate happened to be open. See NegotiationState.settlement_steps_offered
# for the companion fix on the no-figure discount-request path.
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


def _settlement_candidate_specs(total: Decimal) -> list[tuple[Tier, int, Cadence, Decimal]]:
    """Every settlement shape at a given total -- one lump sum, or split
    across 2-3 payments across every multi-payment cadence -- same table
    T3 has always used at the ceiling (see _candidate_specs), just
    parameterised on `total` so the same enumeration also works for an
    intermediate discount step (see _settlement_step_offer): a customer
    who can't pay $950 today should still be able to take 5% off, split
    across payments, not just none-or-full-ceiling."""
    return [(Tier.SETTLEMENT, 1, Cadence.ONCE, total)] + [
        (Tier.SETTLEMENT, n, c, total) for n in (2, 3) for c in _MULTI_PAYMENT_CADENCES
    ]


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
    specs += _settlement_candidate_specs(settlement_total)
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
    NO_AGREEMENT verdict.

    The opening anchor (full balance, one payment, today) is excluded
    unconditionally, regardless of `state.offered` -- rule 3 requires it
    spoken directly on the turn after the Mini-Miranda disclosure, never
    through a tool call, so it can never land in `state.offered` on its
    own; every candidate this function returns represents a genuinely new
    counter-offer, and re-selecting the exact thing the customer already
    heard and (by definition, since they're still negotiating) declined
    would not be new. Baked in here rather than into `state.offered`
    itself so the exclusion applies to every NEW counter this function
    selects, in every caller, without also blocking a customer's own LIVE
    proposal from repairing back into that same shape elsewhere (see
    NegotiationState.offered)."""
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
        if (tier, n, cadence) in state.offered or (tier, n, cadence) == _OPENING_ANCHOR_KEY:  # step 1
            continue
        dates = _payment_dates(start, cadence, n)
        if dates[-1] >= boundary:  # step 3 -- exclusive, see _collect_violations
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
        # Step 5-6: a not-yet-tried (tier, n) combination first, then the
        # total collected (descending), then tier order, then fewer
        # payments, then the consumer's stated cadence.
        #
        # The freshness term exists because all three cadence variants of a
        # given (tier, n) sort adjacently under total/tier-order-then-n
        # alone, so a consumer who never mentioned timing would hear the
        # same money on three different schedules before selection ever
        # moved to a different tier or payment count -- e.g. "$300 today
        # and $300 on August 20th", then the same split again with only the
        # second date moved. Projecting cadence away from `state.offered`
        # gives the set of (tier, n) pairs already tried at ANY cadence; a
        # candidate whose (tier, n) is in that set, and whose cadence isn't
        # the consumer's own, is deprioritised behind anything still
        # genuinely fresh -- but never excluded, so it's still selected
        # once fresh combinations run out (nothing becomes unreachable this
        # way).
        #
        # `-total` comes right after freshness, ahead of tier order: the
        # ladder is enumerated T1..T4 by legal ceiling, not by value, and T3
        # (settlement) sits ahead of T4 (payment plan) in that table even
        # though T4 always collects more. Sorting by tier order first meant
        # a fully-affordable payment plan lost to a settlement purely
        # because of table position -- $200 conceded to a consumer who
        # could afford the full balance. Ranking value first means a
        # settlement only wins once every full-balance option (at every
        # tier, every remaining cadence) is either unaffordable or already
        # offered and refused -- reachable *and* actually the best deal,
        # not just the first thing on the list. Ties at equal value (every
        # full-balance tier shares the same total) still fall through to
        # tier order below, so FULL still outranks DOWNPAYMENT_PLUS_ONE
        # still outranks PAYMENT_PLAN at parity. A settlement is a
        # concession for affordability or for a figure the customer
        # actually named -- never a default just because the gate happened
        # to be open.
        #
        # `cadence != cadence_hint` is load-bearing: a candidate matching
        # the consumer's stated cadence must never be penalised as stale,
        # or a consumer who explicitly asks for weekly after being offered
        # monthly at the same (tier, n) would have that request outranked
        # by an unrelated fresh combination they never asked for.
        offered_tier_n = {(tier, n) for tier, n, _cadence in state.offered}

        def sort_key(c: tuple) -> tuple[int, Decimal, int, int, int]:
            tier, n, cadence, total = c[0], c[1], c[2], c[3]
            stale = (tier, n) in offered_tier_n and cadence != cadence_hint
            return (1 if stale else 0, -total, _TIER_ORDER[tier], n, 0 if cadence == cadence_hint else 1)

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


def _settlement_step_offer(balance: Decimal, call_date: date, state: NegotiationState, step: int) -> Offer:
    """The Nth graduated discount step's offer (see _settlement_step_total),
    enumerated across 1-3 payments exactly like the settlement ceiling
    (_settlement_candidate_specs) instead of always a single lump sum --
    the fix for a real gap: a customer who can't pay $950 today couldn't
    take 5% off at all before this, only none-or-the-full-ceiling.

    Selects the fewest payments that fit `state.capacity` (front-loading,
    same principle as every other tier -- prefer the simplest shape that
    still works); unknown capacity keeps the single-payment shape this
    always had before splitting existed. If capacity is known but nothing
    even at 3 payments fits it, falls back to the most-split candidate --
    the closest thing to affordable, same spirit as _select_counter's own
    step-7 fallback, though this is a single fixed total, not a search
    over the whole ladder, so there's no `unreachable_offer_made` flag to
    set here.

    Every candidate at every step is already at or above the floor by
    construction given today's constants -- the shallowest step's 3-way
    split is still less discounted, so less demanding on the floor, than
    the ceiling's already-floor-clearing 3-way split (see the module's
    MIN_PAYMENT_PCT/MAX_SETTLEMENT_DISCOUNT_PCT relationship). Filtered
    explicitly anyway, not merely assumed, so a future change to either
    constant can't silently offer an illegal split.

    Not reflected in `state.offered` -- same reasoning as the single-
    payment version this replaces (see NegotiationState.
    settlement_steps_offered): every step, at every payment count, shares
    its (tier, n, cadence) key with the ceiling's own candidates, and
    marking any of them "offered" would make _select_counter treat that
    shape as exhausted for every other caller too, including the
    customer's own later specific proposal."""
    step_total = _settlement_step_total(balance, step)
    start = call_date
    if not state.date_unlocked:
        start = min(start, call_date + timedelta(days=MAX_FIRST_PAYMENT_DELAY_DAYS))
    boundary = _add_calendar_months(call_date, MAX_PLAN_DURATION_MONTHS)
    floor = _floor(balance)

    candidates = []
    for tier, n, cadence, total in _settlement_candidate_specs(step_total):
        dates = _payment_dates(start, cadence, n)
        if dates[-1] >= boundary:  # exclusive, see _collect_violations
            continue
        payments = _split_payments(total, n)
        if any(p < floor for p in payments):
            continue
        candidates.append((n, cadence, total, payments, dates))

    reachable = [c for c in candidates if state.capacity is None or max(c[3]) <= state.capacity]
    if reachable:
        n, cadence, total, payments, dates = min(reachable, key=lambda c: c[0])
    else:
        n, cadence, total, payments, dates = max(candidates, key=lambda c: c[0])

    offer = Offer(tier=Tier.SETTLEMENT, total=total, payments=payments, dates=dates, cadence=cadence)
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
        # None, not an explanation of why -- see Verdict.agent_note. `reason`
        # is already spoken and complete (escalate, call over); there is
        # nothing left to correct and no reason to call this tool again, so
        # there is nothing an agent_note would do here except teach the
        # model that account state is something to remember instead of
        # re-check.
        agent_note=None,
        offer_summary=None,
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


def _offer_summary(offer: Offer) -> dict:
    """Structured, already-speech-formatted form of an offer, for
    Verdict.offer_summary -- every amount and date pre-rendered through
    _speak_amount/_speak_date exactly as the agent would say them, so
    confirming terms (rule 5) never needs to reformat a raw Decimal or ISO
    date itself (that reformatting is where a trailing ".00" or an
    expanded date list creeps back in)."""
    return {
        "tier": offer.tier.value,
        "total": _speak_amount(offer.total),
        "payment_count": len(offer.payments),
        "payments": [_speak_amount(p) for p in offer.payments],
        "dates": [_speak_date(d) for d in offer.dates],
    }


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


def _next_offer_reason(offer: Offer, minimum_payment: Decimal | None = None) -> str:
    # Deliberately NOT _counter_reason's "I can't approve that" framing --
    # there is no consumer proposal being refused here, just an offer being
    # volunteered (see request_next_offer). minimum_payment, when given,
    # leads instead of trailing like _counter_reason's -- there's no illegal
    # proposal here for it to be a caveat on, just a stated capacity that
    # couldn't be honored, so the constraint IS the reason being led with,
    # not an addendum to one.
    if minimum_payment is not None:
        return f"The smallest payment I can accept is {_speak_amount(minimum_payment)} -- I can offer {_describe_offer(offer)}."
    return f"I can offer {_describe_offer(offer)}."


# --- Public API ---
def validate_proposal(balance: Decimal, proposal: Proposal, call_date: date, state: NegotiationState) -> Verdict:
    balance = _quantize(balance)

    sanity_violation = _sanity_violation(proposal)
    if sanity_violation is not None:
        offer = opening_offer(balance, call_date)
        return Verdict(
            decision="COUNTER",
            reason=_OPENING_REASON,
            accepted_offer=None,
            counter_offer=offer,
            violations=["degenerate_input"],
            agent_note=sanity_violation,
            offer_summary=_offer_summary(offer),
        )

    total = _quantize(proposal.total)
    n = proposal.number_of_payments
    cadence = proposal.cadence
    first_date = proposal.first_payment_date
    # Quantized once here, used everywhere below instead of proposal.payments
    # directly -- an explicit split arrives however the caller happened to
    # format it (e.g. a bare `300.0` from a JSON tool call, not `300.00`),
    # and every consumer of it downstream (accepted/counter Offers, string
    # formatting for persistence and speech) needs consistent cents.
    payments_given = [_quantize(p) for p in proposal.payments] if proposal.payments is not None else None

    # The consumer's numbers signal capacity; most recent statement wins,
    # whether or not this particular proposal turns out to be legal. An
    # explicit split states their largest single payment directly ($600 of
    # $600/$400 -- they can clearly manage $600 in one go); averaging that
    # across payments would under-read it and silently filter out
    # candidates they could in fact afford. Only a bare total/count (no
    # split given) falls back to the average.
    state.capacity = max(payments_given) if payments_given is not None else _quantize(total / n)

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

        # Second spend condition, independent of the first: even when
        # excluding T3 doesn't change *selection*, the gate must still be
        # spent if the consumer's own proposal is itself a legal settlement
        # that would ACCEPT the moment the gate unlocks (e.g. a legal 10%
        # settlement proposed outright) -- otherwise it takes three asks
        # instead of two to reach an offer the consumer already qualified
        # for on their first try. Pure read against the proposal's own
        # shape; touches no mutable state, so no copy is needed here.
        proposal_dates = _payment_dates(first_date, cadence, n)
        proposal_payments = payments_given if payments_given is not None else _split_payments(total, n)
        proposal_violations = _collect_violations(
            total, n, proposal_payments, proposal_dates, tier, balance, call_date
        )
        if state.date_unlocked:
            proposal_violations = [v for v in proposal_violations if v != "first_payment_too_late"]
        would_accept_if_unlocked = not proposal_violations

        selection_changed = with_t3 is not None and _offer_key(with_t3) != _offer_key(without_t3)
        if selection_changed or would_accept_if_unlocked:
            state.discount_counters_issued += 1
        state.offered.add(_offer_key(without_t3))

        # Floor context (section 8): the proposal's own payments, not the
        # counter's -- surfaced whenever what the consumer asked for would
        # have breached the floor, regardless of whether the gate was
        # actually spent.
        proposed_payments = payments_given if payments_given is not None else _split_payments(total, n)
        minimum_payment = _floor(balance) if any(p < _floor(balance) for p in proposed_payments) else None

        return Verdict(
            decision="COUNTER",
            reason=_counter_reason(without_t3, minimum_payment),
            accepted_offer=None,
            counter_offer=without_t3,
            violations=["discount_gate_locked"],
            minimum_payment=minimum_payment,
            agent_note=_GATE_LOCKED_AGENT_NOTE,
            offer_summary=_offer_summary(without_t3),
        )

    dates = _payment_dates(first_date, cadence, n)
    payments = payments_given if payments_given is not None else _split_payments(total, n)

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
            agent_note=None,
            offer_summary=_offer_summary(accepted),
        )

    # Illegal proposal at a tier the consumer has already reached (T3 only
    # reaches this point once its gate is unlocked -- see above): try the
    # old minimal in-tier repair first. Only fall back to full selection if
    # that repair reproduces something already offered and refused, OR if
    # it doesn't respect what the consumer said they could afford -- repair
    # fixes legality (e.g. raising a too-deep discount to the settlement
    # ceiling) without ever checking affordability, and the ceiling can
    # exceed a capacity that a cheaper, still-legal candidate would fit
    # (e.g. a $400-capacity consumer minimally repaired up to $800 in one
    # payment, when settlement split across two payments of $400 was right
    # there). Selection already scores on capacity; repair does not, so
    # defer to selection whenever repair's own answer doesn't fit.
    repaired = _repair_offer(total, n, cadence, first_date, balance, call_date, date_gate_unlocked=date_was_unlocked)
    # One cent of tolerance: `state.capacity` is an average (_quantize(total
    # / n)) whenever the consumer didn't state an explicit split, but an
    # even split's largest payment is always rounded UP to absorb the
    # remainder (_split_payments) and can therefore exceed that average by
    # exactly one cent even when repair changed nothing about the amount or
    # count -- e.g. $1,000/3 averages to $333.33 but splits to
    # 333.34/333.33/333.33. Without this tolerance, a repair that only
    # shifted the date (proposal already at the right amount and count)
    # would be wrongly rejected as "over capacity" on rounding noise alone.
    # A genuine mismatch (D2's motivating case: $800 repaired against a
    # $400 capacity) is unaffected -- it exceeds by dollars, not a cent.
    repaired_within_capacity = (
        state.capacity is None or max(repaired.payments) <= state.capacity + _CENTS
    )
    if _offer_key(repaired) not in state.offered and repaired_within_capacity:
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
        agent_note=_explain_violations(violations),
        offer_summary=_offer_summary(offer),
    )


def request_next_offer(
    balance: Decimal, call_date: date, state: NegotiationState, customer_capacity: Decimal | None = None,
) -> Verdict:
    """Read-only counterpart to `validate_proposal`, for when the consumer
    has named no figure of their own at all -- deflected, said "I don't
    know", stayed silent on amount. Runs the same capacity-scored
    selection `validate_proposal` uses internally (`_select_counter`) and
    returns the next not-yet-offered candidate, without requiring the
    caller to fabricate a full-balance proposal just to get a response out
    of this module. `nominal_start` is always `call_date` and
    `cadence_hint` is always ONCE -- there is no consumer-stated date or
    cadence to anchor on here, unlike validate_proposal's callers.

    The opening anchor (full balance, one payment, today) is excluded from
    selection: by the system prompt's mandated call flow, this is never
    the first thing said in a call -- the agent has already spoken that
    exact anchor directly, and (per the "ask once" rule) already asked the
    open-ended capacity question, before ever reaching for this tool.
    Without this exclusion, a state where nothing has been offered yet
    (the common case -- this is usually the FIRST tool call of the
    negotiation) would select that exact same candidate again, repeating
    what the consumer just declined. Enforced by `_select_counter` itself
    (unconditionally, for every caller), not by adding it to
    `state.offered` -- `validate_proposal` must still be free to accept,
    or repair a customer's own LIVE proposal to, a full-balance-in-one-
    payment offer exactly as before; only NEW counter-offer selection
    excludes it, never the customer's own proposal.

    `customer_capacity` is the fix for a real interface gap: the prompt's
    capacity question ("what's the most you could put down today...")
    answers with a bare first-payment figure, not a complete proposal --
    there is no total, count, cadence, or date to resolve it into, so
    forcing it through `validate_proposal` means fabricating the rest
    (previously: a hallucinated total_amount paired with a payments list
    that didn't sum to it, rejected by `_is_sane` every time with no way
    for the agent to learn why). Passing that bare figure here instead sets
    `state.capacity` from it -- exactly like validate_proposal already does
    from a real proposal's payments -- and runs the same capacity-scored
    selection. None (the default) leaves `state.capacity` untouched, same
    as before this parameter existed. Never touches the concession gate
    either way -- that only moves in response to a proposed total, and a
    bare capacity figure isn't one. Does mutate `state.offered` (and, via
    `_select_counter`, `state.unreachable_offer_made`) exactly like
    validate_proposal's own counter-offer paths, so the same candidate is
    never volunteered twice.

    A stated `customer_capacity` below the floor sets `minimum_payment` on
    the returned Verdict and states it in `reason` -- exactly like
    validate_proposal already does when a live proposal's own payments
    breach the floor (see `Verdict.minimum_payment`). Selection itself is
    unaffected: nothing below the floor is ever a candidate to begin with,
    so `_select_counter` still lands on the closest legal thing (its own
    step-7 fallback, since nothing fits a below-floor capacity). Without
    this, that fallback offer arrives with no explanation of why it's more
    than what the consumer just said they could pay -- the single most
    likely thing an uncooperative tester says first, and previously the
    one case where the agent had to explain a real constraint with nothing
    in the verdict to explain it from.
    """
    balance = _quantize(balance)
    minimum_payment = None
    if customer_capacity is not None:
        state.capacity = _quantize(customer_capacity)
        floor = _floor(balance)
        if state.capacity < floor:
            minimum_payment = floor
    # No probe copy needed -- _select_counter excludes the opening anchor
    # unconditionally now (see its own docstring), so calling it directly
    # against the real state already can't re-select that candidate, and
    # already mutates state.unreachable_offer_made/state.offered in place
    # exactly like validate_proposal's own counter-offer paths.
    offer = _select_counter(
        balance, call_date, state, nominal_start=call_date, cadence_hint=Cadence.ONCE,
        exclude_settlement=not state.discount_unlocked,
    )

    if offer is None:
        return _no_agreement_verdict()

    state.offered.add(_offer_key(offer))
    return Verdict(
        decision="COUNTER",
        reason=_next_offer_reason(offer, minimum_payment),
        accepted_offer=None,
        counter_offer=offer,
        violations=[],
        minimum_payment=minimum_payment,
        # None, not a "nothing's wrong" note: agent_note means "change what
        # you're doing," and this is the routine path -- a note here would
        # just teach the agent to skim the field, including on the call
        # right after where it actually needs to be read.
        agent_note=None,
        offer_summary=_offer_summary(offer),
    )


def resolve_proposal_total(total_amount: Decimal | None, payments: list[Decimal] | None) -> Decimal | None:
    """The one derivation shared by `negotiate()` (its own step 1) and
    app/tools.py's gate-shopping guard, which needs to know the resolved
    total *before* deciding whether to even call into this module (to
    reuse a cached gate verdict instead). A single source of truth so the
    two can't drift -- see this module's own D1 history for exactly what
    happens when a derivation is duplicated across sites and only one
    copy gets updated: the guard would silently misclassify what counts
    as a discount ask, and the concession gate would become shoppable
    within a turn again.

    payments given, total_amount absent -> sum(payments). Otherwise
    total_amount is returned unchanged (including None) -- an explicit
    total_amount is never overwritten, even if it disagrees with
    payments; that disagreement is a real error for `_sanity_violation`
    to catch, not something this function silently resolves one way.
    Never raises: a malformed payments list (non-numeric entries) simply
    leaves total_amount unresolved, same as if payments had been absent."""
    if total_amount is None and payments is not None:
        try:
            return sum(payments)
        except TypeError:
            return None
    return total_amount


def negotiate(
    balance: Decimal,
    call_date: date,
    state: NegotiationState,
    total_amount: Decimal | None = None,
    payments: list[Decimal] | None = None,
    customer_capacity: Decimal | None = None,
    cadence: Cadence | None = None,
    first_payment_date: date | None = None,
    number_of_payments: int | None = None,
    discount_requested: bool = False,
) -> Verdict:
    """Single read-only entry point, replacing the choice between
    `validate_proposal` and `request_next_offer` -- a choice gpt-4o-mini
    could not reliably make in practice (see the live failure this
    function exists to fix: "$200 today" encoded as
    total_amount=1000, payments=[200], because there was no field for a
    bare capacity figure). Every argument here is optional; this function
    alone decides, from which ones are actually present, whether the
    caller is validating a proposal or asking for the next offer to
    volunteer.

    Delegates entirely to `validate_proposal`/`request_next_offer` below --
    neither one changes, so every negotiation rule (ceiling, floor,
    window, tier classification, capacity scoring, both gates, rounding,
    `offered`) is exactly as tested. This function only resolves
    arguments into a call to one of them (or, for `discount_requested`,
    composes the same primitives -- `_select_counter`, the gate counter --
    those two functions already use internally); it never decides what's
    acceptable itself.

    Resolution order:
    1. `payments` given, `total_amount` absent -> total_amount =
       sum(payments). If both are given and they disagree, this step does
       NOT run (total_amount is not absent) -- the mismatch reaches
       `validate_proposal` exactly as given, and its own sanity check
       rejects it with an agent_note naming the mismatch, exactly as
       before. This function must never silently prefer one over the
       other by overwriting a stated total_amount.
    2. `payments` given -> number_of_payments = len(payments), even if a
       conflicting number_of_payments was also supplied; the array is
       authoritative.
    3. `payments` or `total_amount` present (after 1-2) -> this is a
       proposal. A missing (None) cadence defaults to ONCE for a single
       payment, BIWEEKLY otherwise; a missing first_payment_date defaults
       to call_date; a missing number_of_payments defaults to 1. A
       present-but-malformed value is passed through unchanged rather
       than defaulted -- only genuine absence gets a default, so
       `_sanity_violation` still catches and explains anything actually
       wrong. If `customer_capacity` is also given, it overrides whatever
       `validate_proposal` itself derives from the proposal (an explicit
       statement beats an inferred one) -- applied after the call
       returns, because `validate_proposal` always sets `state.capacity`
       from the proposal internally as its very first step and there is
       no way to ask it not to without changing that function.
       `discount_requested` is ignored here -- a real proposal already
       carries its own discount-ness (`_classify_tier`'s total < balance
       check), so there's nothing this flag would add.
    4. No proposal, `discount_requested=True` -- the customer asked for a
       reduction without naming a figure, the case this flag exists for
       (the honest alternative to inventing a total just to say so). If
       `customer_capacity` is also given, it's applied first (affects
       selection below, same as it would filter any other candidate).
       - Gate still locked: spend it -- unconditionally; there is no
         customer-stated total to test the "did excluding T3 actually
         change anything" question validate_proposal's own gate-locked
         branch asks, so there's nothing to condition the spend on -- and
         counter with the best available non-discount option
         (`_select_counter`, settlement excluded, opening anchor excluded
         exactly like `request_next_offer`, since this may be the first
         tool call after that anchor was spoken directly).
       - Gate already unlocked: the customer is holding their ground on a
         further discount ask with still no number. Rather than the
         settlement ceiling immediately (the maximum the ladder allows,
         handed out on the very first no-figure ask once unlocked), this
         walks SETTLEMENT_STEP_COUNT equal steps from a shallow discount
         up to the ceiling -- state.settlement_steps_offered advances by
         one each call, capped at the ceiling. Each step is itself split
         across 1-3 payments exactly like the ceiling, capacity-scored to
         the fewest that fit (see _settlement_step_offer) -- a discount is
         still worth offering to a consumer who can't pay that step's
         total in one go. Offered directly, not through ordinary
         tier-order selection (which would prefer a lower-tier-order
         non-discount candidate over settlement every time, the opposite
         of what was just asked for).
    5. No proposal, `discount_requested=False`, `customer_capacity` given
       -> the next-offer path, with capacity set from it.
    6. Nothing present at all -> the next-offer path, capacity unchanged.
    """
    if payments is not None:
        total_amount = resolve_proposal_total(total_amount, payments)
        number_of_payments = len(payments)

    if payments is not None or total_amount is not None:
        n = number_of_payments if number_of_payments is not None else 1
        resolved_cadence = cadence if cadence is not None else (
            Cadence.ONCE if n == 1 else Cadence.BIWEEKLY
        )
        resolved_date = first_payment_date if first_payment_date is not None else call_date
        proposal = Proposal(
            total=total_amount,
            number_of_payments=n,
            cadence=resolved_cadence,
            first_payment_date=resolved_date,
            payments=payments,
        )
        verdict = validate_proposal(balance, proposal, call_date, state)
        if customer_capacity is not None:
            state.capacity = _quantize(customer_capacity)
        return verdict

    if discount_requested:
        if customer_capacity is not None:
            state.capacity = _quantize(customer_capacity)

        if not state.discount_unlocked:
            state.discount_counters_issued += 1
            # No probe copy needed -- _select_counter excludes the opening
            # anchor unconditionally (see its own docstring).
            offer = _select_counter(
                balance, call_date, state, nominal_start=call_date, cadence_hint=Cadence.ONCE,
                exclude_settlement=True,
            )
            if offer is None:
                return _no_agreement_verdict()
            state.offered.add(_offer_key(offer))
            return Verdict(
                decision="COUNTER",
                reason=_counter_reason(offer),
                accepted_offer=None,
                counter_offer=offer,
                violations=["discount_gate_locked"],
                agent_note=_GATE_LOCKED_AGENT_NOTE,
                offer_summary=_offer_summary(offer),
            )

        # Gate unlocked: walk the discount in steps rather than handing out
        # the maximum the instant it opens -- see NegotiationState.
        # settlement_steps_offered and negotiate()'s docstring. The gate
        # controls WHEN a discount becomes available, not HOW MUCH; a
        # customer who names an actual figure is capped by their own
        # number ($900 gets $900), so a customer who names nothing
        # shouldn't be capped at the deepest discount the ladder allows.
        # Each step is itself split across 1-3 payments, capacity-scored
        # exactly like the ceiling -- see _settlement_step_offer -- so a
        # customer who can't manage the step's total in one payment still
        # gets to take the discount, just spread out, instead of the
        # module silently treating "can't pay it today" as "can't have it."
        state.settlement_steps_offered = min(state.settlement_steps_offered + 1, SETTLEMENT_STEP_COUNT)
        offer = _settlement_step_offer(balance, call_date, state, state.settlement_steps_offered)
        return Verdict(
            decision="COUNTER",
            reason=_next_offer_reason(offer),
            accepted_offer=None,
            counter_offer=offer,
            violations=[],
            agent_note=None,
            offer_summary=_offer_summary(offer),
        )

    return request_next_offer(balance, call_date, state, customer_capacity=customer_capacity)
