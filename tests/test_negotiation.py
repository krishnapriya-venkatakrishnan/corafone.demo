"""app/negotiation.py: pure-function tests, no network, no API keys, no
async. Dedicated tests where the assertion needs to inspect the returned
Offer's shape (exact payments/dates), not just ACCEPT vs COUNTER."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import negotiation as neg

BALANCE = Decimal("1000.00")
CALL_DATE = date(2026, 7, 18)


def _proposal(total, n, cadence=neg.Cadence.MONTHLY, first_date=CALL_DATE, payments=None):
    return neg.Proposal(
        total=Decimal(str(total)),
        number_of_payments=n,
        cadence=cadence,
        first_payment_date=first_date,
        payments=[Decimal(str(p)) for p in payments] if payments is not None else None,
    )


def _fresh_state():
    return neg.NegotiationState()


def _unlocked_state():
    """For tests that check settlement-tier *validity* in isolation from the
    concession gate -- a fresh state would otherwise counter every legal
    discount proposal once, regardless of amount, which is a separate
    mechanism covered by the dedicated gate tests."""
    return neg.NegotiationState(discount_counters_issued=neg.MAX_DISCOUNT_COUNTERS)


# --- The outcome ladder: one test per tier/shape ---
def test_full_payment_accepts():
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.tier == neg.Tier.FULL
    assert verdict.accepted_offer.payments == [Decimal("1000.00")]


def test_canonical_downpayment_plus_one_accepts():
    proposal = _proposal(1000, 2, cadence=neg.Cadence.BIWEEKLY, payments=["750", "250"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.payments == [Decimal("750"), Decimal("250")]


def test_max_settlement_even_split_accepts():
    proposal = _proposal(800, 3, payments=["266.67", "266.67", "266.66"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.tier == neg.Tier.SETTLEMENT


def test_uneven_and_legal_default_basis_accepts_as_proposed():
    proposal = _proposal(875, 3, payments=["350", "275", "250"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.payments == [Decimal("350"), Decimal("275"), Decimal("250")]


def test_uneven_floor_breach_default_basis_counters():
    proposal = _proposal(800, 3, payments=["350", "250", "200"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert "payment_below_floor" in verdict.violations
    assert verdict.counter_offer.payments == [Decimal("266.67"), Decimal("266.67"), Decimal("266.66")]


def test_ambiguity_case_counters_under_chosen_reading():
    """The 25% floor is ambiguous only on the settlement tier: 25% of the
    original $1,000 balance ($250) or 25% of the agreed $800 total ($200)?
    $300/$300/$200 is exactly the case that tells the two readings apart --
    legal under agreed-total (last payment == $200 == the floor), illegal
    under original-balance (last payment < $250). Chosen: original balance,
    the stricter reading -- it can never grant terms beyond what's clearly
    authorised. This test is the executable record of that choice."""
    proposal = _proposal(800, 3, payments=["300", "300", "200"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert "payment_below_floor" in verdict.violations


def test_front_loaded_floor_breach_counters():
    proposal = _proposal(800, 3, payments=["500", "200", "100"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert "payment_below_floor" in verdict.violations
    assert verdict.counter_offer.payments == [Decimal("266.67"), Decimal("266.67"), Decimal("266.66")]


def test_back_loaded_same_amounts_counters_identically():
    front = neg.validate_proposal(
        BALANCE, _proposal(800, 3, payments=["500", "200", "100"]), CALL_DATE, _unlocked_state()
    )
    back = neg.validate_proposal(
        BALANCE, _proposal(800, 3, payments=["100", "200", "500"]), CALL_DATE, _unlocked_state()
    )

    assert front.decision == back.decision == "COUNTER"
    assert front.counter_offer.payments == back.counter_offer.payments


# --- A discount request with an illegal split must still hit the gate at
# full balance, never get repaired up to the maximum legal discount because
# something else needed fixing. All of these use a fresh (locked) state.
#
# `gate_spent` is RE-BASELINED per-case (was unconditionally True): the gate
# is now only spent when excluding T3 actually changed what got selected
# (see defect 2 -- capacity that can't reach any settlement candidate either
# must not cost the consumer their one counter for nothing).
#   - 700/1: capacity 700 reaches T3 n=2 ($400) but not T4 n=2 ($500) once
#     T3 is excluded -> selection genuinely differs -> spent.
#   - 500/2: capacity 250 (500/2) can't reach ANY settlement candidate
#     ($266.67 is the cheapest) -- T4 n=4 ($250) is picked either way ->
#     not spent.
#   - 100/1: capacity 100 can't reach anything at all, including T4 -- both
#     probes fall back to the same global-cheapest step-7 candidate ->
#     not spent.
#   - 800/3 uneven [500,200,100]: capacity is the *largest single payment*
#     (500, not the 266.67 average -- see defect 3), which reaches T3 n=2
#     but not T4 n=2 once excluded -> spent, same shape as 700/1. ---
@pytest.mark.parametrize(
    "total,n,payments,gate_spent",
    [
        (700, 1, None, True),  # 30% off, no split at all
        (500, 2, None, False),  # 50% off, capacity too low to reach T3 either way
        (100, 1, None, False),  # 90% off, capacity too low to reach anything
        (800, 3, ["500", "200", "100"], True),  # legal ceiling, illegal split
    ],
)
def test_gate_intercepts_illegal_discount_before_repair(total, n, payments, gate_spent):
    proposal = _proposal(total, n, cadence=neg.Cadence.MONTHLY, payments=payments)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert state.discount_counters_issued == (1 if gate_spent else 0)


def test_discount_too_deep_hits_locked_gate_before_ceiling_repair():
    """$600 is a discount request, so with a fresh (locked) gate it hits the
    no-discount ladder at full balance -- the ceiling repair never runs."""
    proposal = _proposal(600, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert state.discount_counters_issued == 1


def test_discount_too_deep_repairs_to_ceiling_once_unlocked():
    """The ceiling-repair mechanism itself, isolated from the gate."""
    proposal = _proposal(600, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert "discount_too_deep" in verdict.violations
    assert verdict.counter_offer.total == Decimal("800.00")


def test_settlement_tier_caps_at_three_payments():
    proposal = _proposal(800, 4, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert len(verdict.counter_offer.payments) == 3


def test_delayed_start_loophole_preserves_structure_pulls_start_back():
    """Two violations at once (duration and first-payment-delay) still
    repair to one minimal fix -- only the start date moves; amount, count,
    and cadence stay exactly as proposed."""
    proposal = _proposal(1000, 3, cadence=neg.Cadence.MONTHLY, first_date=CALL_DATE + timedelta(days=60))
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert set(verdict.violations) == {"duration_exceeds_window", "first_payment_too_late"}
    offer = verdict.counter_offer
    assert offer.total == Decimal("1000.00")
    assert len(offer.payments) == 3
    assert offer.cadence == neg.Cadence.MONTHLY
    assert offer.dates[0] == CALL_DATE + timedelta(days=14)


def test_boundary_exactly_800_accepts():
    verdict = neg.validate_proposal(
        BALANCE, _proposal(800, 1, cadence=neg.Cadence.ONCE), CALL_DATE, _unlocked_state()
    )
    assert verdict.decision == "ACCEPT"


def test_boundary_799_99_counters():
    verdict = neg.validate_proposal(
        BALANCE, _proposal("799.99", 1, cadence=neg.Cadence.ONCE), CALL_DATE, _fresh_state()
    )
    assert verdict.decision == "COUNTER"


def test_boundary_monthly_four_from_today_accepts():
    verdict = neg.validate_proposal(
        BALANCE, _proposal(1000, 4, cadence=neg.Cadence.MONTHLY, first_date=CALL_DATE), CALL_DATE, _fresh_state()
    )
    assert verdict.decision == "ACCEPT"


def test_boundary_monthly_four_from_tomorrow_counters():
    verdict = neg.validate_proposal(
        BALANCE,
        _proposal(1000, 4, cadence=neg.Cadence.MONTHLY, first_date=CALL_DATE + timedelta(days=1)),
        CALL_DATE,
        _fresh_state(),
    )
    assert verdict.decision == "COUNTER"
    assert "duration_exceeds_window" in verdict.violations


def test_rounding_sums_exactly():
    """Remainder cents distribute front-to-back, one at a time: $1,000/3 is
    333.34/333.33/333.33."""
    proposal = _proposal(1000, 3, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.accepted_offer.payments == [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")]
    assert sum(verdict.accepted_offer.payments) == Decimal("1000.00")


def test_gate_first_discount_ask_counters_with_next_tier():
    """RE-BASELINED (was T2 $750/$250): counters now select on capacity, not
    on preserving the consumer's own payment count. $900/2 implies a $450
    per-payment capacity -- below T2's $750 leading payment, so T2 isn't
    reachable. The closest reachable arrangement is a 3-payment plan at
    $333.34/333.33/333.33 (fewer payments preferred over the 4-payment
    plan), at the consumer's own biweekly cadence. See the module
    docstring's capacity-scored Selection algorithm -- this is the same
    fix as the $200-capacity/$1,000-in-one-payment bug, just triggered a
    tier earlier."""
    proposal = _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert "discount_gate_locked" in verdict.violations
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.cadence == neg.Cadence.BIWEEKLY
    assert verdict.counter_offer.payments == [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")]
    assert state.discount_counters_issued == 1


def test_gate_consumer_holds_accepts():
    proposal = _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY)
    state = neg.NegotiationState(discount_counters_issued=1)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "ACCEPT"


def test_gate_counter_preserves_consumers_cadence():
    """RE-BASELINED (was T2, 2 monthly payments): same capacity-driven shift
    as test_gate_first_discount_ask_counters_with_next_tier above -- $900/2
    implies $450 capacity, T2 is unreachable, selection lands on a 3-payment
    plan instead. The cadence-preservation behavior this test is actually
    about still holds at the new tier/count: proposing monthly cadence gets
    countered a month apart, not forced to a different rhythm."""
    proposal = _proposal(900, 2, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.cadence == neg.Cadence.MONTHLY
    assert verdict.counter_offer.dates == [
        CALL_DATE, neg._add_calendar_months(CALL_DATE, 1), neg._add_calendar_months(CALL_DATE, 2),
    ]


# --- Property-style: payments always sum exactly to the total ---
@pytest.mark.parametrize(
    "total,n",
    [("1000.00", 3), ("800.00", 3), ("500.00", 4), ("333.33", 2), ("1000.00", 1)],
)
def test_split_payments_sums_exactly(total, n):
    payments = neg._split_payments(Decimal(total), n)
    assert sum(payments) == Decimal(total)
    assert len(payments) == n


# --- Degenerate input: never raises, always COUNTER ---
@pytest.mark.parametrize(
    "kwargs",
    [
        {"total": "0", "n": 1},
        {"total": "-100", "n": 1},
        {"total": "1000", "n": 5},  # one past the ladder's own outer bound
        {"total": "1000", "n": 6},
        {"total": "1000", "n": 7},
        {"total": "1000", "n": "three"},  # non-integer count
    ],
)
def test_degenerate_numeric_input_never_raises(kwargs):
    proposal = neg.Proposal(
        total=Decimal(kwargs["total"]),
        number_of_payments=kwargs["n"],
        cadence=neg.Cadence.MONTHLY,
        first_payment_date=CALL_DATE,
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())
    assert verdict.decision == "COUNTER"


def test_floor_reachable_by_even_split_at_odd_balance():
    """An even split must always be able to reach the floor -- not just at
    balances that divide cleanly. $137.50/4 has no clean division either
    way, and still accepts."""
    balance = Decimal("137.50")
    verdict = neg.validate_proposal(
        balance, _proposal(137.50, 4, cadence=neg.Cadence.MONTHLY), CALL_DATE, _fresh_state()
    )

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.payments == [
        Decimal("34.38"), Decimal("34.38"), Decimal("34.37"), Decimal("34.37"),
    ]
    assert sum(verdict.accepted_offer.payments) == balance


def test_degenerate_unparseable_date_never_raises():
    proposal = neg.Proposal(
        total=Decimal("1000"),
        number_of_payments=1,
        cadence=neg.Cadence.ONCE,
        first_payment_date="not-a-date",  # bypasses the type hint on purpose
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())
    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer is not None


def test_degenerate_huge_payment_count_never_raises():
    """An astronomically large payment count (STT mishearing "3" as
    "100000") must counter, not overflow calendar-month arithmetic."""
    proposal = neg.Proposal(
        total=Decimal("1000"),
        number_of_payments=100000,
        cadence=neg.Cadence.MONTHLY,
        first_payment_date=CALL_DATE,
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())
    assert verdict.decision == "COUNTER"


def test_degenerate_huge_payment_count_never_raises_weekly():
    proposal = neg.Proposal(
        total=Decimal("1000"),
        number_of_payments=100000,
        cadence=neg.Cadence.WEEKLY,
        first_payment_date=CALL_DATE,
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())
    assert verdict.decision == "COUNTER"


def test_past_dated_first_payment_repairs_to_today():
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE, first_date=CALL_DATE - timedelta(days=365))
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert "first_payment_in_past" in verdict.violations
    assert verdict.counter_offer.dates == [CALL_DATE]


def test_overpayment_caps_at_balance():
    proposal = _proposal(1200, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert "overpayment" in verdict.violations
    assert verdict.counter_offer.total == Decimal("1000.00")


# --- The settlement ceiling/floor arithmetic at a balance other than
# $1,000, since that's the arithmetic most likely to hide a bug at one
# fixed value. ---

# --- Capacity-scored selection (replaces minimal-repair counter-offer
# selection -- see the module docstring). ---
def test_capacity_200_counters_full_plan_not_lump_sum():
    """The motivating bug: consumer signals $200 capacity, gate fires. The
    old repair-based counter preserved their n=1 shape and re-offered
    "$1,000 in one payment" -- exactly what they just refused, even though
    $250 x 4 was legal and reachable throughout. Capacity-scored selection
    must reach $250 x 4 monthly instead, with no discount conceded."""
    proposal = _proposal(200, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.total == Decimal("1000.00")  # no discount conceded
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4
    assert verdict.counter_offer.cadence == neg.Cadence.MONTHLY
    assert state.capacity == Decimal("200.00")


def test_five_payments_hits_too_many_payments_not_degenerate_input():
    """Raised sanity cap (24, was 4): "$200 a month for five months" is the
    most natural thing a delinquent consumer says and must reach
    too_many_payments/selection, not short-circuit to the opening offer."""
    proposal = _proposal(1000, 5, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert "too_many_payments" in verdict.violations
    assert "degenerate_input" not in verdict.violations
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4


def test_refusing_monthly_still_offers_biweekly_then_weekly():
    """`offered` is keyed on (tier, n, cadence), not just tier/n -- refusing
    3 monthly must not permanently block 3 biweekly and 3 weekly; they must
    still be reachable eventually.

    RE-BASELINED: 3-biweekly is no longer the *immediate* next counter.
    Selection now deprioritises cadence-only variants of an already-tried
    (tier, n) behind any genuinely fresh (tier, n) combination (this
    stage's fix -- a consumer who never mentioned timing shouldn't hear the
    same money on three schedules before selection tries something
    structurally different). Capacity here is $300 (the largest of the
    proposal's own uneven payments), which also reaches a fresh PAYMENT_PLAN
    n=4 candidate -- fresher than the now-stale SETTLEMENT n=3 cadence
    variants, so it's interposed before biweekly. Once every reachable
    fresh combination at this capacity is exhausted (settlement n=3 and
    payment_plan n=4 are all that fit both the window and $300), selection
    falls back to the remaining cadence variants of those two, in tier
    order -- settlement (tier order 2) before payment_plan (tier order 3).
    Biweekly and weekly are still both reached, just not back-to-back."""
    state = _unlocked_state()
    illegal = _proposal(800, 3, cadence=neg.Cadence.MONTHLY, payments=["200", "300", "300"])

    first = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert first.counter_offer.tier == neg.Tier.SETTLEMENT
    assert first.counter_offer.cadence == neg.Cadence.MONTHLY

    second = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert second.counter_offer.tier == neg.Tier.PAYMENT_PLAN  # fresh (tier, n), preferred over stale cadence variants
    assert second.counter_offer.cadence == neg.Cadence.MONTHLY

    third = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert third.counter_offer.tier == neg.Tier.SETTLEMENT
    assert third.counter_offer.cadence == neg.Cadence.BIWEEKLY

    fourth = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert fourth.counter_offer.tier == neg.Tier.SETTLEMENT
    assert fourth.counter_offer.cadence == neg.Cadence.WEEKLY


def test_legal_uneven_split_accepted_as_proposed_not_normalised():
    """A legal $600/$400 down-payment-plus-one must be accepted exactly as
    proposed -- never normalised to the canonical $750/$250 candidate."""
    proposal = _proposal(1000, 2, cadence=neg.Cadence.BIWEEKLY, payments=["600", "400"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.payments == [Decimal("600"), Decimal("400")]


def test_legal_900_over_two_accepted_at_900_not_countered_at_800():
    """With the gate unlocked, $900/2 is a legal settlement proposal (above
    the $800 ceiling) and must be accepted at $900 -- selection must not
    override a legal consumer proposal with a different candidate."""
    proposal = _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.total == Decimal("900.00")
    assert verdict.accepted_offer.payments == [Decimal("450.00"), Decimal("450.00")]


# --- The date gate: symmetric with the discount gate (counter once, then
# accept on hold), except the hard 3-month window is never relaxed. ---
def test_late_first_payment_counters_once_then_accepts_on_hold():
    state = _fresh_state()
    late = _proposal(1000, 1, cadence=neg.Cadence.ONCE, first_date=CALL_DATE + timedelta(days=20))

    first = neg.validate_proposal(BALANCE, late, CALL_DATE, state)
    assert first.decision == "COUNTER"
    assert "first_payment_too_late" in first.violations
    assert state.date_counters_issued == 1

    second = neg.validate_proposal(BALANCE, late, CALL_DATE, state)
    assert second.decision == "ACCEPT"
    assert second.accepted_offer.dates == [CALL_DATE + timedelta(days=20)]


def test_late_first_payment_beyond_three_months_never_accepted():
    """The hard duration window is never relaxed by the date gate, however
    many times the consumer holds their position."""
    state = neg.NegotiationState(date_counters_issued=1)  # date gate already unlocked
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE, first_date=CALL_DATE + timedelta(days=100))
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert "duration_exceeds_window" in verdict.violations


def test_legal_consumer_stated_date_preserved_not_clamped_to_today():
    """A legal date beyond today (but within the 14-day soft cap) must be
    preserved exactly, not clamped to today -- someone who says "the 28th"
    shouldn't be countered with "today", which reads as not listening."""
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE, first_date=CALL_DATE + timedelta(days=10))
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.dates == [CALL_DATE + timedelta(days=10)]


# --- Exhaustion: capacity too low for every candidate, or every candidate
# already offered and refused. ---
def test_capacity_below_every_candidate_offers_cheapest_once():
    """$50 is below every single candidate's largest payment (T4/4's $250
    is the cheapest on the whole ladder) -- step 7: offer the cheapest
    thing that still fits the window, once."""
    proposal = _proposal(50, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert len(verdict.counter_offer.payments) == 4
    assert max(verdict.counter_offer.payments) == Decimal("250.00")


# --- Defect 1: the unreachable-capacity fallback (step 7) is offered at
# most once per call, not walked candidate-by-candidate in ascending price
# order on every refusal. ---
def test_unreachable_capacity_offers_cheapest_once_then_no_agreement():
    """Capacity $100 is below every candidate on the ladder (T4/4's $250 is
    the cheapest). First refusal gets that cheapest offer; a second
    refusal at the same capacity must escalate, not walk up to the next
    cheapest candidate ($266.67, then $333.34, then...) -- that ladder
    climb is the defect this test guards against."""
    proposal = _proposal(100, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()

    first = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)
    assert first.decision == "COUNTER"
    assert first.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert max(first.counter_offer.payments) == Decimal("250.00")
    assert state.unreachable_offer_made is True

    second = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)
    assert second.decision == "NO_AGREEMENT"
    assert second.counter_offer is None
    assert second.accepted_offer is None


def test_unreachable_capacity_then_higher_capacity_resumes_normal_selection():
    """The one-time fallback flag must not block genuine progress: once the
    consumer names a figure that actually reaches something, selection
    resumes normally rather than treating every subsequent call as
    exhausted."""
    state = _fresh_state()
    neg.validate_proposal(BALANCE, _proposal(100, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)
    assert state.unreachable_offer_made is True

    verdict = neg.validate_proposal(BALANCE, _proposal(400, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)
    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer is not None
    assert max(verdict.counter_offer.payments) <= Decimal("400.00")


# --- Defect 2: the discount gate is spent only when excluding T3 actually
# changed the outcome -- a capacity statement that can't reach a settlement
# candidate either must not cost the consumer their one counter for free. ---
def test_gate_not_spent_when_no_settlement_candidate_was_reachable_anyway():
    """Capacity $200 (from a $200 single-payment ask) can't reach any T3
    candidate ($266.67 is the cheapest) -- excluding T3 changes nothing, so
    the gate must not be spent. A genuine, later discount request must
    still hit the no-discount ladder, not sail through as if already
    unlocked."""
    state = _fresh_state()
    first = neg.validate_proposal(BALANCE, _proposal(200, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)
    assert first.decision == "COUNTER"
    assert first.counter_offer.total == Decimal("1000.00")  # no discount conceded
    assert state.discount_counters_issued == 0

    second = neg.validate_proposal(BALANCE, _proposal(800, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)
    assert second.decision == "COUNTER"
    assert second.accepted_offer is None


def test_gate_spent_when_a_settlement_candidate_was_genuinely_excluded():
    """Capacity $500 (from a $500 single-payment ask) *can* reach a T3
    candidate (n=2 at $400) that isn't reachable once T3 is dropped -- the
    gate genuinely withheld a discount here, so it must be spent. The
    returned counter is still full balance, never the discount."""
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(500, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert verdict.counter_offer.tier != neg.Tier.SETTLEMENT
    assert state.discount_counters_issued == 1


# --- Defect 3: capacity reads the consumer's largest *stated* payment, not
# an average that under-reads an explicit uneven split. ---
def test_capacity_reads_largest_stated_payment_not_average():
    proposal = _proposal(1000, 2, cadence=neg.Cadence.BIWEEKLY, payments=["600", "400"])
    state = _fresh_state()
    neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert state.capacity == Decimal("600")


def test_capacity_falls_back_to_average_when_no_split_given():
    proposal = _proposal(1000, 2, cadence=neg.Cadence.BIWEEKLY)
    state = _fresh_state()
    neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert state.capacity == Decimal("500.00")


# --- Defect 4: the floor minimum is surfaced on the gate branch too,
# keyed on the proposal's own payments, not just on the general repair
# branch, and only when the floor was actually breached. ---
def test_minimum_payment_surfaced_on_gate_branch_when_floor_breached():
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(200, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.minimum_payment == Decimal("250.00")
    assert "$250" in verdict.reason


def test_minimum_payment_absent_on_gate_branch_when_floor_not_breached():
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY), CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.minimum_payment is None


def test_exhausted_candidates_yields_no_agreement():
    """Once every legal, in-window arrangement has already been offered and
    refused, there is nothing left to select -- NO_AGREEMENT, not a repeat
    of something already refused."""
    all_non_settlement_keys = {
        (tier, n, cadence)
        for tier, n, cadence, _ in neg._candidate_specs(BALANCE)
        if tier != neg.Tier.SETTLEMENT
    }
    state = neg.NegotiationState(offered=all_non_settlement_keys)
    proposal = _proposal(50, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "NO_AGREEMENT"
    assert verdict.counter_offer is None
    assert verdict.accepted_offer is None


def test_deferred_start_shrinks_reachable_candidates():
    """Deferring the start reduces which candidates can even fit the
    3-month window -- at capacity $250 and a 72-day deferral, every
    4-payment candidate (even weekly, the most schedule-compact cadence)
    falls outside the window; only 3-payment candidates fit, and their
    $333.34 payment exceeds the stated $250 capacity. Selection falls back
    to the cheapest still-fitting candidate (step 7) rather than offering
    something that doesn't fit the window at all. Isolated from the 14-day
    delay-cap gate (already unlocked) so only the window-fit mechanism is
    under test."""
    state = neg.NegotiationState(date_counters_issued=1)  # discount gate locked, date gate unlocked
    deferred_start = CALL_DATE + timedelta(days=72)
    proposal = _proposal(250, 1, cadence=neg.Cadence.ONCE, first_date=deferred_start)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert len(verdict.counter_offer.payments) == 3
    assert verdict.counter_offer.payments == [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")]
    assert verdict.counter_offer.dates[-1] <= neg._add_calendar_months(CALL_DATE, neg.MAX_PLAN_DURATION_MONTHS)


# --- T2 cadence collapse: T2's split is fixed by the floor regardless of
# cadence, so cadence there only ever shifted the second payment's date --
# enumerating all three multi-payment cadences produced three
# near-identical counters in a row before selection ever reached a
# materially different tier. See _T2_CADENCES. ---
def test_candidate_specs_yields_exactly_one_downpayment_plus_one_entry():
    specs = neg._candidate_specs(BALANCE)
    t2_specs = [s for s in specs if s[0] == neg.Tier.DOWNPAYMENT_PLUS_ONE]

    assert len(t2_specs) == 1
    _, n, cadence, total = t2_specs[0]
    assert n == 2
    assert cadence == neg.Cadence.BIWEEKLY
    assert total == BALANCE


def test_consumer_proposed_t2_monthly_still_accepted_as_proposed():
    """The collapse only changes what selection *offers* -- a consumer who
    proposes the legal $750/$250 split on their own chosen (non-biweekly)
    cadence must still be accepted on it, per "Consumer proposals still
    take precedence"."""
    proposal = _proposal(1000, 2, cadence=neg.Cadence.MONTHLY, payments=["750", "250"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.cadence == neg.Cadence.MONTHLY
    assert verdict.accepted_offer.payments == [Decimal("750"), Decimal("250")]


def test_repeated_800_request_never_repeats_the_same_counter_back_to_back():
    """RE-BASELINED scenario (was 5 rounds, the last 3 of which included two
    near-identical $750/$250 counters differing only in the second
    payment's date): collapsing T2 to one cadence means consecutive
    counters are never the same split on the same first date."""
    state = neg.NegotiationState()
    proposal = _proposal(800, 1, cadence=neg.Cadence.ONCE)

    seen = []
    for _ in range(6):
        verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)
        if verdict.decision != "COUNTER":
            break
        offer = verdict.counter_offer
        seen.append((offer.payments, offer.dates[0]))

    assert verdict.decision == "ACCEPT"
    assert len(seen) == 2  # was 4 before the collapse
    for (payments_a, first_a), (payments_b, first_b) in zip(seen, seen[1:]):
        assert not (payments_a == payments_b and first_a == first_b)


# --- Freshness-preferred selection: a not-yet-tried (tier, n) is preferred
# over a cadence-only variant of a (tier, n) already offered, so a consumer
# who never mentioned timing doesn't hear the same money on three
# schedules before selection tries something structurally different. ---
def test_repeated_unreachable_ask_varies_tier_or_n_before_cadence():
    """While a genuinely fresh (tier, n) combination is still reachable, it
    must be preferred over a cadence-only variant of something already
    offered -- no two consecutive counters may share both payments and
    first payment date until every fresh combination is exhausted."""
    state = _unlocked_state()
    illegal = _proposal(800, 3, cadence=neg.Cadence.MONTHLY, payments=["200", "300", "300"])

    offered_tier_n_before_call: set[tuple[neg.Tier, int]] = set()
    seen = []
    fresh_exhausted_at = None
    for i in range(8):
        verdict = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
        if verdict.decision != "COUNTER":
            break
        offer = verdict.counter_offer
        was_stale = (offer.tier, len(offer.payments)) in offered_tier_n_before_call
        if was_stale and fresh_exhausted_at is None:
            fresh_exhausted_at = i
        seen.append((offer.payments, offer.dates[0]))
        offered_tier_n_before_call.add((offer.tier, len(offer.payments)))

    assert fresh_exhausted_at is not None, "expected the fresh (tier, n) pool to run out within 8 rounds"
    for i, ((payments_a, first_a), (payments_b, first_b)) in enumerate(zip(seen, seen[1:])):
        if i + 1 < fresh_exhausted_at:
            assert not (payments_a == payments_b and first_a == first_b)


def test_first_counter_unaffected_by_freshness_term_when_nothing_offered_yet():
    """The freshness term is 0 for every candidate on a call where nothing
    has been offered yet, so a first counter is exactly what plain
    tier-order-then-n-then-cadence selection would already have picked."""
    verdict = neg.validate_proposal(
        BALANCE, _proposal(200, 1, cadence=neg.Cadence.ONCE), CALL_DATE, _fresh_state()
    )

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4
    assert verdict.counter_offer.cadence == neg.Cadence.MONTHLY


def test_consumer_signalled_cadence_still_honored_after_tier_n_already_tried():
    """A cadence the consumer explicitly states must never be penalised as
    stale, even when its (tier, n) was already offered at a different
    cadence -- otherwise their stated preference would be outranked by an
    unrelated fresh combination they never asked for."""
    state = _unlocked_state()
    illegal_monthly = _proposal(800, 3, cadence=neg.Cadence.MONTHLY, payments=["200", "300", "300"])
    first = neg.validate_proposal(BALANCE, illegal_monthly, CALL_DATE, state)
    assert first.counter_offer.tier == neg.Tier.SETTLEMENT
    assert first.counter_offer.cadence == neg.Cadence.MONTHLY

    illegal_weekly = _proposal(800, 3, cadence=neg.Cadence.WEEKLY, payments=["200", "300", "300"])
    second = neg.validate_proposal(BALANCE, illegal_weekly, CALL_DATE, state)

    assert second.counter_offer.tier == neg.Tier.SETTLEMENT
    assert second.counter_offer.cadence == neg.Cadence.WEEKLY


def test_exhaustion_with_everything_offered_still_yields_no_agreement():
    """The freshness term only reorders selection -- it must never make a
    candidate permanently unreachable. With every legal in-window
    combination already in `offered` (gate unlocked, so settlement is in
    play too), an illegal proposal whose in-tier repair also collides with
    `offered` falls through to selection and finds nothing -- NO_AGREEMENT
    with no offer, exactly as before this change."""
    all_keys = {(tier, n, cadence) for tier, n, cadence, _ in neg._candidate_specs(BALANCE)}
    state = neg.NegotiationState(discount_counters_issued=1, offered=all_keys)
    illegal = _proposal(800, 3, cadence=neg.Cadence.MONTHLY, payments=["200", "300", "300"])
    verdict = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)

    assert verdict.decision == "NO_AGREEMENT"
    assert verdict.counter_offer is None
    assert verdict.accepted_offer is None


# --- Speech-shaped reasons (section 8): no raw ISO dates, no ".00", and
# the floor is only surfaced when payment_below_floor actually fires. ---
def test_reason_text_has_no_raw_iso_date_or_trailing_zero_cents():
    verdict = neg.validate_proposal(
        BALANCE, _proposal(800, 1, cadence=neg.Cadence.ONCE), CALL_DATE, _unlocked_state()
    )
    assert verdict.decision == "ACCEPT"
    assert "2026-07-18" not in verdict.reason
    assert ".00" not in verdict.reason
    assert "$800" in verdict.reason


def test_minimum_payment_surfaced_only_on_floor_violation():
    floor_breach = neg.validate_proposal(
        BALANCE, _proposal(800, 3, payments=["500", "200", "100"]), CALL_DATE, _unlocked_state()
    )
    assert "payment_below_floor" in floor_breach.violations
    assert floor_breach.minimum_payment == Decimal("250.00")
    assert "$250" in floor_breach.reason

    clean_accept = neg.validate_proposal(
        BALANCE, _proposal(1000, 1, cadence=neg.Cadence.ONCE), CALL_DATE, _fresh_state()
    )
    assert clean_accept.minimum_payment is None


def test_settlement_ceiling_and_floor_at_different_balance():
    balance = Decimal("2500.00")

    too_deep = neg.validate_proposal(
        balance, _proposal("1999.99", 1, cadence=neg.Cadence.ONCE), CALL_DATE, neg.NegotiationState(discount_counters_issued=1)
    )
    assert too_deep.decision == "COUNTER"
    assert too_deep.counter_offer.total == Decimal("2000.00")  # 80% of 2500

    at_ceiling = neg.validate_proposal(
        balance, _proposal("2000.00", 1, cadence=neg.Cadence.ONCE), CALL_DATE, neg.NegotiationState(discount_counters_issued=1)
    )
    assert at_ceiling.decision == "ACCEPT"
