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
# `gate_spent` is RE-BASELINED to all-False (was per-case, mixed True/False):
# selection now ranks by total collected first, tier order only as a
# tie-break (see the "prefer the highest-value reachable arrangement" fix).
# PAYMENT_PLAN n=4's floor (25% of balance) is always below SETTLEMENT n=3's
# floor (26.67% of balance, since MAX_SETTLEMENT_DISCOUNT_PCT=20% leaves 80%
# split three ways), so whenever a settlement candidate is reachable at all,
# a payment-plan candidate collecting the full balance is too -- and now
# always wins on value. Excluding T3 therefore stops changing what
# _select_counter picks, for every capacity level, so the first gate-spend
# clause (selection actually differed) can no longer fire from a bare total.
# All four cases below land on the same PAYMENT_PLAN counter with or without
# T3 in the pool -- none of them spend the gate through that clause, and
# none of these totals are themselves a legal settlement (the second,
# independent spend clause -- see test_gate_spent_when_a_settlement_
# candidate_was_genuinely_excluded for a case that still spends it). ---
@pytest.mark.parametrize(
    "total,n,payments,gate_spent",
    [
        (700, 1, None, False),  # 30% off, no split at all
        (500, 2, None, False),  # 50% off, capacity too low to reach T3 either way
        (100, 1, None, False),  # 90% off, capacity too low to reach anything
        (800, 3, ["500", "200", "100"], False),  # legal ceiling, illegal split
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
    no-discount ladder at full balance -- the ceiling repair never runs.

    RE-BASELINED: the gate is not spent here. $600 capacity still reaches
    PAYMENT_PLAN n=4 ($250 leading payment), which collects the full
    balance and now outranks every settlement candidate on value -- see
    test_gate_intercepts_illegal_discount_before_repair's header comment.
    Excluding T3 changes nothing, and $600 is well below this balance's
    $800 settlement ceiling, so it wouldn't ACCEPT once unlocked either."""
    proposal = _proposal(600, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert state.discount_counters_issued == 0


def test_discount_too_deep_repairs_to_ceiling_once_unlocked():
    """The ceiling-repair mechanism itself, isolated from the gate --
    _repair_offer directly, not through validate_proposal.

    RE-BASELINED to call `_repair_offer` directly instead of going through
    `validate_proposal`. It can no longer observe the raw repair in
    isolation there: for a single-payment proposal, `state.capacity` is
    derived from that same payment (there's no split to state a higher
    figure with), so it can never exceed the proposal's own total -- and a
    too-deep discount's repaired ceiling is, by definition, always higher
    than the total that triggered the repair. `repaired_within_capacity`
    is therefore always False here, and validate_proposal always falls
    through to `_select_counter` instead of returning the repaired offer
    -- see the next test for what that actually returns now under the
    capacity-scored, value-first fix."""
    offer = neg._repair_offer(
        total=Decimal("600"), n=1, cadence=neg.Cadence.ONCE,
        first_date=CALL_DATE, balance=BALANCE, call_date=CALL_DATE,
    )

    assert offer.tier == neg.Tier.SETTLEMENT
    assert offer.total == Decimal("800.00")


def test_discount_too_deep_single_payment_falls_through_to_selection_once_unlocked():
    """The validate_proposal-level counterpart to the test above: a
    too-deep single-payment discount ask can never itself state a capacity
    above its own total (see that test's docstring), so its repaired
    ceiling ($800) is always over capacity ($600) and selection takes
    over -- landing on PAYMENT_PLAN's full balance, the highest-value
    reachable arrangement, rather than the discount ceiling."""
    proposal = _proposal(600, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert "discount_too_deep" in verdict.violations
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.total == Decimal("1000.00")


def test_settlement_tier_caps_at_three_payments():
    """Uses an explicit front-loaded split (largest payment $350) so the
    consumer's stated capacity comfortably covers the repaired 3-payment
    settlement ($266.67/payment) -- isolating the tier-cap repair itself
    from D2's separate capacity check (see
    test_settlement_repair_falls_through_to_selection_when_over_capacity),
    which would otherwise reject this repair for an unrelated reason
    (capacity too low) rather than demonstrating the cap."""
    proposal = _proposal(800, 4, cadence=neg.Cadence.MONTHLY, payments=["350", "150", "150", "150"])
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.SETTLEMENT
    assert len(verdict.counter_offer.payments) == 3


def test_settlement_repair_falls_through_to_selection_when_over_capacity():
    """D2: repair must respect capacity. A bare $800/4 proposal (no split
    given) implies a $200 per-payment capacity (800/4); the tier-cap repair
    would still produce a 3-payment settlement at $266.67/payment --
    exceeding that capacity by more than a cent (real, not rounding noise).
    Repair is rejected and selection takes over, landing on the cheapest
    thing that fits the window: PAYMENT_PLAN n=4 biweekly at the $250
    floor (monthly n=4 no longer fits the exclusive 3-month window -- see
    D1), which -- as a side effect -- also recovers the full balance
    instead of granting an unaffordable discount."""
    proposal = _proposal(800, 4, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert verdict.counter_offer.cadence == neg.Cadence.BIWEEKLY
    assert len(verdict.counter_offer.payments) == 4


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


def test_boundary_monthly_four_from_today_now_exceeds_window():
    """RE-BASELINED (was ACCEPT): D1 -- the 3-month window is now exclusive,
    so a payment landing exactly on the three-calendar-month anniversary of
    call_date is outside it. Four monthly payments starting today put the
    last payment exactly on that anniversary (92 days out), so this is now
    illegal; the repaired counter falls back to biweekly (monthly n=4 no
    longer fits from today at all)."""
    verdict = neg.validate_proposal(
        BALANCE, _proposal(1000, 4, cadence=neg.Cadence.MONTHLY, first_date=CALL_DATE), CALL_DATE, _fresh_state()
    )
    assert verdict.decision == "COUNTER"
    assert "duration_exceeds_window" in verdict.violations
    assert verdict.counter_offer.cadence == neg.Cadence.BIWEEKLY
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4


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


# --- D3: the discount gate is spent on EITHER of two independent
# conditions -- excluding T3 changed selection, OR the consumer's own
# proposal would legally ACCEPT the moment the gate unlocks. Before this
# fix, a legal settlement proposal that happened not to change selection
# (Tier 2 outranks Tier 3 in tier order either way) could sail past the
# gate for free on the *second* ask instead of spending it on the first --
# taking three asks total to reach acceptance instead of two. ---
def test_gate_spent_on_first_ask_when_proposal_itself_is_a_legal_settlement():
    """$900 as a single payment is already a legal 10% settlement (above
    the $800 ceiling, no other violations). Tier order alone would already
    prefer DOWNPAYMENT_PLUS_ONE over SETTLEMENT regardless of whether T3 is
    excluded, so clause 1 (selection changed) alone would NOT spend the
    gate here -- clause 2 (would ACCEPT if unlocked) must. One counter,
    then accept on the second ask, not the third."""
    proposal = _proposal(900, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()

    first = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)
    assert first.decision == "COUNTER"
    assert state.discount_counters_issued == 1

    second = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)
    assert second.decision == "ACCEPT"
    assert second.accepted_offer.total == Decimal("900.00")


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
    way, and still accepts.

    RE-BASELINED cadence (was MONTHLY): under D1's now-exclusive 3-month
    window, four monthly payments starting today no longer fit (see
    test_boundary_monthly_four_from_today_now_exceeds_window) -- unrelated
    to what this test is actually about (split-rounding arithmetic), so it
    now uses WEEKLY, which still fits comfortably, to keep the two concerns
    isolated."""
    balance = Decimal("137.50")
    verdict = neg.validate_proposal(
        balance, _proposal(137.50, 4, cadence=neg.Cadence.WEEKLY), CALL_DATE, _fresh_state()
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
    must reach $250 x 4 instead, with no discount conceded (biweekly, not
    monthly -- see the RE-BASELINED note below)."""
    proposal = _proposal(200, 1, cadence=neg.Cadence.ONCE)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert verdict.counter_offer.total == Decimal("1000.00")  # no discount conceded
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4
    # RE-BASELINED cadence (was MONTHLY): D1's now-exclusive 3-month window
    # means monthly n=4 from today no longer fits, so the default cadence
    # for a 4-payment plan is now biweekly (the next entry in
    # _MULTI_PAYMENT_CADENCES once monthly is filtered out).
    assert verdict.counter_offer.cadence == neg.Cadence.BIWEEKLY
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


# --- Verdict.agent_note / Verdict.offer_summary -- the interface-gap fix
# for the loop where the agent called validate_consumer_proposal(total_
# amount=1000, number_of_payments=1, payments=[200]) five times, got the
# same garbled-input reason every time, and had no way to learn what was
# wrong. agent_note is a second, machine-readable channel: never spoken,
# always present on a non-ACCEPT verdict, explaining exactly what the
# agent should do differently. ---
def test_sum_mismatch_payments_gets_a_specific_agent_note():
    """The exact failing call from the live loop: total_amount=1000,
    number_of_payments=1, payments=[200] -- sum(payments) is 200 but
    total_amount is 1000, so _is_sane rejects it (same as before this
    change), but now agent_note names exactly what's wrong instead of
    leaving the agent to repeat the identical call."""
    proposal = neg.Proposal(
        total=Decimal("1000"), number_of_payments=1, cadence=neg.Cadence.ONCE,
        first_payment_date=CALL_DATE, payments=[Decimal("200")],
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert verdict.agent_note == "payments sum to $200 but total_amount was $1000 -- they must match"


def test_payments_count_mismatch_gets_a_specific_agent_note():
    proposal = neg.Proposal(
        total=Decimal("1000"), number_of_payments=1, cadence=neg.Cadence.ONCE,
        first_payment_date=CALL_DATE, payments=[Decimal("500"), Decimal("500")],
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert verdict.agent_note == "payments has 2 entries but number_of_payments was 1"


def test_cadence_once_multi_payment_gets_a_specific_agent_note():
    proposal = neg.Proposal(
        total=Decimal("1000"), number_of_payments=2, cadence=neg.Cadence.ONCE, first_payment_date=CALL_DATE,
    )
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert verdict.agent_note == "cadence 'once' requires exactly one payment, but number_of_payments was 2"


def test_agent_note_present_on_discount_gate_locked_counter():
    """RE-BASELINED: the note is now an instruction to call the tool again,
    not an explanation of how the gate works -- see Verdict.agent_note's
    docstring for the live failure (the model reasoning from a stated rule
    instead of re-querying) that motivated the change."""
    proposal = _proposal(600, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.agent_note == neg._GATE_LOCKED_AGENT_NOTE


def test_agent_note_present_on_ordinary_violation_counter():
    """The everyday repair/select COUNTER path (a real violation, gate not
    involved) also gets an agent_note -- built from the same violation
    codes already in `violations`, worded for the agent instead of a log."""
    proposal = _proposal(1000, 5, cadence=neg.Cadence.MONTHLY)  # too_many_payments
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "COUNTER"
    assert "too_many_payments" in verdict.violations
    assert "more payments than this tier allows" in verdict.agent_note


def test_agent_note_absent_on_no_agreement():
    """RE-BASELINED (was test_agent_note_present_on_no_agreement, asserting
    "every legal arrangement has been offered and refused"): NO_AGREEMENT
    is terminal -- `reason` is already spoken and complete, and there's
    nothing left to correct or call the tool again for, so an agent_note
    here would only be state to reason from, not an instruction to act on.
    See Verdict.agent_note's docstring."""
    state = _fresh_state()
    state.unreachable_offer_made = True
    proposal = _proposal(50, 24, cadence=neg.Cadence.WEEKLY)  # nothing reachable, fallback spent
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "NO_AGREEMENT"
    assert verdict.agent_note is None
    assert verdict.offer_summary is None


def test_agent_note_absent_on_accept():
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.decision == "ACCEPT"
    assert not verdict.agent_note


def test_offer_summary_matches_the_accepted_offer():
    proposal = _proposal(800, 2, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _unlocked_state())

    assert verdict.decision == "ACCEPT"
    assert verdict.offer_summary == {
        "tier": "settlement",
        "total": "$800",
        "payment_count": 2,
        "payments": ["$400", "$400"],
        "dates": [neg._speak_date(CALL_DATE), neg._speak_date(neg._add_calendar_months(CALL_DATE, 1))],
    }


def test_refusing_monthly_still_offers_biweekly_then_weekly():
    """`offered` is keyed on (tier, n, cadence), not just tier/n -- refusing
    3 monthly must not permanently block 3 biweekly and 3 weekly; they must
    still be reachable eventually.

    RE-BASELINED (second time): selection now ranks reachable candidates by
    total collected first, tier order only as a tie-break among candidates
    of EQUAL staleness (see the "prefer the highest-value reachable
    arrangement" fix) -- freshness still dominates value, so a genuinely
    untried (tier, n) always wins over a stale one regardless of total (the
    fresh PAYMENT_PLAN n=4 biweekly counter on the second call is
    unaffected by this stage's change, same as before). What changes is
    what happens once *every* (tier, n) combination reachable at this
    capacity has been tried at least once: PAYMENT_PLAN n=4's remaining
    cadence (weekly, $1000) now outranks SETTLEMENT n=3's cadence variants
    ($800) even though both are equally "stale," because value is compared
    before tier order among stale candidates too. So payment_plan's two
    reachable cadences (biweekly, then weekly) are exhausted before
    settlement's three are touched at all -- settlement only starts
    appearing on the fourth call, once payment_plan has nothing fresh OR
    stale-but-higher-value left to offer. Weekly is still reached for both
    tiers, just later than before: a fifth call is needed to see
    settlement's weekly variant."""
    state = _unlocked_state()
    illegal = _proposal(800, 3, cadence=neg.Cadence.MONTHLY, payments=["200", "300", "300"])

    first = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert first.counter_offer.tier == neg.Tier.SETTLEMENT
    assert first.counter_offer.cadence == neg.Cadence.MONTHLY

    second = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert second.counter_offer.tier == neg.Tier.PAYMENT_PLAN  # fresh (tier, n), preferred over stale cadence variants
    # D1's now-exclusive 3-month window excludes monthly n=4 from today's
    # candidate pool entirely, so the fresh PAYMENT_PLAN n=4 candidate is
    # reached at biweekly instead.
    assert second.counter_offer.cadence == neg.Cadence.BIWEEKLY

    third = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    # RE-BASELINED tier (was SETTLEMENT): payment_plan n=4's remaining
    # (stale, but higher-value) weekly cadence now outranks settlement n=3's
    # cadence variants, which are equally stale but worth $200 less.
    assert third.counter_offer.tier == neg.Tier.PAYMENT_PLAN
    assert third.counter_offer.cadence == neg.Cadence.WEEKLY

    fourth = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    # RE-BASELINED cadence (was WEEKLY): payment_plan n=4 is now fully
    # exhausted (all three cadences offered), so settlement n=3 finally
    # gets its turn -- starting from biweekly, same tie-break as before.
    assert fourth.counter_offer.tier == neg.Tier.SETTLEMENT
    assert fourth.counter_offer.cadence == neg.Cadence.BIWEEKLY

    fifth = neg.validate_proposal(BALANCE, illegal, CALL_DATE, state)
    assert fifth.counter_offer.tier == neg.Tier.SETTLEMENT
    assert fifth.counter_offer.cadence == neg.Cadence.WEEKLY


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


def test_gate_spent_when_the_proposal_is_a_legal_settlement_once_unlocked():
    """RENAMED and RE-BASELINED from test_gate_spent_when_a_settlement_
    candidate_was_genuinely_excluded, whose premise the value-first fix
    makes impossible to construct: PAYMENT_PLAN n=4's floor is fixed at
    MIN_PAYMENT_PCT (25%) of the balance, strictly below SETTLEMENT n=3's
    floor (0.8/3 = 26.67%, since MAX_SETTLEMENT_DISCOUNT_PCT=20%). Any
    capacity high enough to reach a settlement candidate is therefore
    always high enough to reach payment_plan n=4 too, which -- now ranked
    by value first -- always wins the comparison. Excluding T3 can no
    longer change what `_select_counter` picks for any single-payment
    capacity figure: the first gate-spend clause (`selection_changed`) is
    unreachable under today's constants. See app/negotiation.py's docstring
    for this finding in full; the check itself is left in place as
    correct, harmless defensive logic, not removed.

    The gate can still be spent, but only through the second, independent
    clause: a proposal that is *already* a legal settlement (at or above
    the 80% floor) and would ACCEPT the moment the gate unlocks. $800 here
    is exactly this $1,000 balance's ceiling. The returned counter is still
    full balance, never the discount -- the gate is locked on this call
    regardless of why it got spent."""
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(800, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert verdict.counter_offer.tier != neg.Tier.SETTLEMENT
    assert state.discount_counters_issued == 1


# --- "Prefer the highest-value reachable arrangement": selection now ranks
# candidates by total collected (descending) ahead of tier order, so a
# fully-affordable payment plan is never passed over for a settlement that
# collects less, purely because settlement sorts earlier in the tier table.
# See _select_counter's sort_key and _TIER_ORDER's comment. ---
def test_capacity_600_gate_spent_offers_full_balance_not_an_800_settlement():
    """The live bug this fix exists for: a $600-capacity consumer, gate
    already spent, must not be countered with an $800 settlement (a $200
    concession) when a fully-affordable $500+$500 payment plan collects
    the whole balance at the same affordability. Both are reachable at
    $600; the payment plan is now preferred because it's worth more, not
    because of table position."""
    state = _unlocked_state()
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state, customer_capacity=Decimal("600"))

    assert verdict.counter_offer.tier != neg.Tier.SETTLEMENT
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert verdict.counter_offer.payments == [Decimal("500.00"), Decimal("500.00")]


def test_capacity_statement_below_balance_does_not_spend_the_gate():
    """A discount-shaped proposal ($400 of $1,000, gate locked) no longer
    spends the gate: PAYMENT_PLAN n=4's floor (25% of balance) is always
    reachable whenever any settlement candidate is, and now always wins on
    value, so excluding T3 never changes what gets selected -- see
    test_gate_spent_when_the_proposal_is_a_legal_settlement_once_unlocked
    for why this makes the gate's first spend clause unreachable under
    today's constants. The consumer was never actually denied a discount
    they could have had, so nothing should be withheld from their one
    counter for free."""
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(400, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert state.discount_counters_issued == 0


def test_discount_requested_still_spends_the_gate_and_reaches_a_settlement_on_the_second_ask():
    """discount_requested's own path (negotiate(), not _select_counter's
    ordinary ranking) is untouched by this fix: a no-figure discount ask
    still spends the gate on the first call and still walks to a genuine
    settlement figure once unlocked."""
    state = neg.NegotiationState()
    first = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)
    assert first.counter_offer.tier != neg.Tier.SETTLEMENT
    assert state.discount_counters_issued == 1

    second = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)
    assert second.counter_offer.tier == neg.Tier.SETTLEMENT
    assert second.counter_offer.total == neg._settlement_step_total(BALANCE, 1)


def test_customer_named_legal_settlement_still_accepted_directly():
    """A consumer who names their own legal settlement figure outright
    ($900, 10% off, within the 20% cap) is unaffected by how selection
    ranks CANDIDATES -- this never reaches _select_counter at all; a legal
    proposal is accepted exactly as proposed."""
    state = _unlocked_state()
    verdict = neg.validate_proposal(BALANCE, _proposal(900, 1, cadence=neg.Cadence.ONCE), CALL_DATE, state)

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.total == Decimal("900.00")


def test_settlements_reachable_once_full_balance_options_are_exhausted():
    """With no capacity restriction, every full-balance (tier, payment
    count) combination -- down-payment-plus-one, and payment plans of 2, 3,
    and 4 -- is offered once (freshness always beats value, so these are
    reached before any settlement candidate) before settlement is ever
    volunteered. Once all four full-balance combinations have been tried
    at least once, settlement -- still fully fresh itself -- is offered
    next. Settlements remain fully reachable; they are simply never the
    first thing offered while a fresh, more valuable alternative remains."""
    state = _unlocked_state()
    tiers_seen = []
    for _ in range(4):
        verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)
        tiers_seen.append(verdict.counter_offer.tier)

    assert neg.Tier.SETTLEMENT not in tiers_seen
    assert tiers_seen.count(neg.Tier.PAYMENT_PLAN) == 3
    assert tiers_seen.count(neg.Tier.DOWNPAYMENT_PLUS_ONE) == 1

    fifth = neg.request_next_offer(BALANCE, CALL_DATE, state)
    assert fifth.counter_offer.tier == neg.Tier.SETTLEMENT


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
    """RE-BASELINED scenario (was 5 rounds, then 4 after the T2 cadence
    collapse; now 2): D3 widens the discount-gate spend condition to also
    fire when the consumer's own proposal would legally ACCEPT once
    unlocked. $800 in one payment is already a legal settlement (exactly at
    the ceiling), so the very first ask now spends the gate -- it no longer
    takes a second, different ask to notice the proposal was legal all
    along. One counter, then accept."""
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
    assert len(seen) == 1
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
    # RE-BASELINED cadence (was MONTHLY): D1's now-exclusive 3-month window
    # excludes monthly n=4 from today's candidate pool, so plain
    # tier-order-then-n-then-cadence selection now lands on biweekly, the
    # next entry in _MULTI_PAYMENT_CADENCES.
    assert verdict.counter_offer.cadence == neg.Cadence.BIWEEKLY


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


# --- request_next_offer: the read-only counterpart for when the consumer
# names no figure at all (deflects, says "I don't know", stays silent). ---
def test_request_next_offer_skips_the_opening_anchor():
    """The very first call, on a state where nothing has gone through a
    tool yet, must NOT return full-balance-in-one-payment -- that's the
    fixed opening anchor, always spoken directly (never via a tool call)
    before this function is ever reached. Returning it again would repeat
    exactly what the consumer just declined."""
    state = _fresh_state()
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier != neg.Tier.FULL
    assert verdict.counter_offer.tier == neg.Tier.DOWNPAYMENT_PLUS_ONE
    assert verdict.counter_offer.payments == [Decimal("750.00"), Decimal("250.00")]


def test_request_next_offer_validate_proposal_still_accepts_full_balance_directly():
    """The opening-anchor exclusion is local to request_next_offer -- a
    consumer who actually agrees to pay the full balance in one payment
    must still ACCEPT normally through validate_proposal."""
    state = _fresh_state()
    proposal = _proposal(1000, 1, cadence=neg.Cadence.ONCE)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.tier == neg.Tier.FULL


def test_request_next_offer_never_repeats_a_candidate():
    state = _fresh_state()
    seen = []
    for _ in range(4):
        verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)
        if verdict.decision != "COUNTER":
            break
        offer = verdict.counter_offer
        key = (offer.tier, len(offer.payments), offer.cadence)
        assert key not in seen
        seen.append(key)
    assert len(seen) > 1


def test_request_next_offer_never_offers_a_discount_while_gate_locked():
    state = _fresh_state()  # discount gate locked
    for _ in range(6):
        verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)
        if verdict.decision != "COUNTER":
            break
        assert verdict.counter_offer.tier != neg.Tier.SETTLEMENT


def test_request_next_offer_can_reach_settlement_once_gate_unlocked():
    state = _unlocked_state()
    tiers_seen = []
    for _ in range(6):
        verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)
        if verdict.decision != "COUNTER":
            break
        tiers_seen.append(verdict.counter_offer.tier)
    assert neg.Tier.SETTLEMENT in tiers_seen


def test_request_next_offer_respects_existing_capacity():
    """Capacity set from an earlier turn (e.g. the consumer said "$200"
    then later deflected with no new figure) must still be honored --
    request_next_offer never resets or ignores it."""
    state = _fresh_state()
    state.capacity = Decimal("300.00")
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert max(verdict.counter_offer.payments) <= Decimal("300.00")
    assert state.capacity == Decimal("300.00")  # untouched


def test_request_next_offer_does_not_touch_capacity_or_gate():
    state = _fresh_state()
    neg.request_next_offer(BALANCE, CALL_DATE, state)

    assert state.capacity is None
    assert state.discount_counters_issued == 0


# --- customer_capacity: the interface gap that caused the loop. The
# prompt's capacity question ("what's the most you could put down today,
# and could you clear the rest on a later date?") answers with a bare
# first-payment figure, not a complete proposal -- there was previously no
# field for that, so the agent fabricated total_amount=1000,
# payments=[200]. "$200 today" now becomes
# request_next_offer(customer_capacity=200). ---
def test_request_next_offer_with_customer_capacity_sets_capacity_and_returns_a_plan():
    """The exact fix scenario: a bare "$200 today" answer becomes
    customer_capacity=200 instead of a fabricated validate_consumer_
    proposal call, and returns the $250 x 4 plan with nothing invented."""
    state = _fresh_state()
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state, customer_capacity=Decimal("200"))

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4
    assert state.capacity == Decimal("200.00")


def test_request_next_offer_customer_capacity_none_leaves_capacity_untouched():
    """Explicit None (the default) is identical to omitting the argument --
    see test_request_next_offer_does_not_touch_capacity_or_gate above."""
    state = _fresh_state()
    neg.request_next_offer(BALANCE, CALL_DATE, state, customer_capacity=None)

    assert state.capacity is None


def test_request_next_offer_customer_capacity_never_touches_the_gate():
    state = _fresh_state()
    neg.request_next_offer(BALANCE, CALL_DATE, state, customer_capacity=Decimal("900"))

    assert state.discount_counters_issued == 0


def test_request_next_offer_reason_never_says_cannot_approve():
    """Distinct phrasing from _counter_reason -- there is no consumer
    proposal being refused here, just an offer being volunteered."""
    state = _fresh_state()
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)

    assert "can't approve" not in verdict.reason
    assert verdict.reason.startswith("I can offer")


def test_request_next_offer_routine_counter_has_no_agent_note():
    """A volunteered offer is the routine path -- nothing for the agent to
    correct, so agent_note must be falsy. Distinct from validate_proposal's
    non-ACCEPT paths, which always carry one: agent_note means "something
    needs your attention," and a note on every routine call would just
    teach the model to skim the field, including the one call where it
    actually matters (NO_AGREEMENT, or a malformed validate_proposal
    call)."""
    state = _fresh_state()
    verdict = neg.request_next_offer(BALANCE, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert not verdict.agent_note


def test_request_next_offer_eventually_reaches_no_agreement():
    state = _fresh_state()
    last = None
    for _ in range(20):
        last = neg.request_next_offer(BALANCE, CALL_DATE, state)
        if last.decision != "COUNTER":
            break
    assert last.decision == "NO_AGREEMENT"
    assert last.counter_offer is None


def test_request_next_offer_unreachable_fallback_offered_once_then_no_agreement():
    """The step-7 "cheapest thing that fits" one-time fallback must still
    only fire once, even when reached via request_next_offer -- confirms
    `unreachable_offer_made` correctly propagates back from the internal
    probe state used to exclude the opening anchor."""
    state = _fresh_state()
    state.capacity = Decimal("50.00")  # below every candidate on the ladder

    first = neg.request_next_offer(BALANCE, CALL_DATE, state)
    assert first.decision == "COUNTER"
    assert state.unreachable_offer_made is True

    second = neg.request_next_offer(BALANCE, CALL_DATE, state)
    assert second.decision == "NO_AGREEMENT"


# --- Re-submitting a tool-sourced offer must preserve its exact split.
# Discovered while verifying request_next_offer's composition with the
# existing confirm/record flow: DOWNPAYMENT_PLUS_ONE candidates from
# _select_counter (the canonical $750/$250 front-loaded split) are NOT an
# even split, but a bare total/count re-submission with no `payments`
# falls back to an even one -- silently replacing the real terms. This is
# pre-existing (reachable through the ordinary discount-gate/select_counter
# path, nothing to do with request_next_offer specifically), and confirmed
# here at both the module level (the failure mode) and as the documented
# fix (passing `payments` preserves it). ---
def test_reoffer_without_payments_field_silently_becomes_even_split():
    """The failure mode this test documents, not the desired behavior --
    a regression guard proving the bug is real, so the fix (next test)
    isn't validating a scenario that could never actually occur."""
    state = _fresh_state()
    gate_ask = _proposal(900, 1, cadence=neg.Cadence.ONCE)
    first = neg.validate_proposal(BALANCE, gate_ask, CALL_DATE, state)
    offer = first.counter_offer
    assert offer.tier == neg.Tier.DOWNPAYMENT_PLUS_ONE
    assert offer.payments == [Decimal("750.00"), Decimal("250.00")]

    resubmitted = _proposal(
        offer.total, len(offer.payments), cadence=offer.cadence, first_date=offer.dates[0],
    )
    second = neg.validate_proposal(BALANCE, resubmitted, CALL_DATE, state)

    assert second.decision == "ACCEPT"
    assert second.accepted_offer.payments == [Decimal("500.00"), Decimal("500.00")]  # NOT what was offered


def test_reoffer_with_payments_field_preserves_the_offered_split():
    """The fix: carrying the offer's own `payments` array forward on
    confirmation preserves the exact front-loaded split -- see the NEVER
    block's "never let re-submitting an offer silently change its shape"
    and the `payments` field's schema description in app/config.py."""
    state = _fresh_state()
    gate_ask = _proposal(900, 1, cadence=neg.Cadence.ONCE)
    first = neg.validate_proposal(BALANCE, gate_ask, CALL_DATE, state)
    offer = first.counter_offer

    resubmitted = _proposal(
        offer.total, len(offer.payments), cadence=offer.cadence, first_date=offer.dates[0],
        payments=[str(p) for p in offer.payments],
    )
    second = neg.validate_proposal(BALANCE, resubmitted, CALL_DATE, state)

    assert second.decision == "ACCEPT"
    assert second.accepted_offer.payments == [Decimal("750.00"), Decimal("250.00")]


def test_request_next_offer_reoffer_with_payments_field_preserves_the_split():
    """Same fix, exercised through request_next_offer's own output (the
    deflection path) rather than the discount-gate path -- confirms the
    composition works end to end for the new tool too."""
    state = _fresh_state()
    first = neg.request_next_offer(BALANCE, CALL_DATE, state)
    offer = first.counter_offer
    assert offer.tier == neg.Tier.DOWNPAYMENT_PLUS_ONE
    assert offer.payments == [Decimal("750.00"), Decimal("250.00")]

    resubmitted = _proposal(
        offer.total, len(offer.payments), cadence=offer.cadence, first_date=offer.dates[0],
        payments=[str(p) for p in offer.payments],
    )
    second = neg.validate_proposal(BALANCE, resubmitted, CALL_DATE, state)

    assert second.decision == "ACCEPT"
    assert second.accepted_offer.payments == [Decimal("750.00"), Decimal("250.00")]


# --- resolve_proposal_total: the single derivation shared by negotiate()
# and app/tools.py's gate-shopping guard, so the two can't drift apart. ---
def test_resolve_proposal_total_derives_from_payments_when_total_absent():
    assert neg.resolve_proposal_total(None, [Decimal("200"), Decimal("300")]) == Decimal("500")


def test_resolve_proposal_total_never_overwrites_an_explicit_total():
    """Even when it disagrees with payments -- that disagreement is
    _sanity_violation's to catch, not this function's to paper over."""
    given = Decimal("1000")
    assert neg.resolve_proposal_total(given, [Decimal("200")]) is given


def test_resolve_proposal_total_neither_given_returns_none():
    assert neg.resolve_proposal_total(None, None) is None


def test_resolve_proposal_total_only_total_given_returns_it_unchanged():
    given = Decimal("500")
    assert neg.resolve_proposal_total(given, None) is given


def test_resolve_proposal_total_malformed_payments_never_raises():
    assert neg.resolve_proposal_total(None, ["not a number"]) is None


# --- negotiate(): the single dispatcher replacing the choice between
# validate_proposal and request_next_offer. Delegates entirely to the two
# functions above (untouched, still tested independently everywhere
# above) -- these tests are about resolution, not decisions: given which
# arguments are present, does negotiate() build the right call. ---
def _decimals(values):
    return [Decimal(str(v)) for v in values]


def test_negotiate_payments_only_derives_total_and_count():
    """payments given, total_amount absent -> total = sum(payments),
    number_of_payments = len(payments). A full-balance uneven split
    (300 + 700 = 1000) must ACCEPT with the consumer's own split
    preserved, exactly as validate_proposal would given the same
    total/count/payments explicitly."""
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, payments=_decimals([300, 700]))

    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.total == Decimal("1000.00")
    assert verdict.accepted_offer.payments == [Decimal("300.00"), Decimal("700.00")]


def test_negotiate_total_only_defaults_to_one_payment_today():
    """total_amount given alone -> number_of_payments defaults to 1,
    cadence to ONCE, first_payment_date to call_date -- the "customer
    named just a total" case. $900 is a discount ask on a locked gate, so
    this exercises the discount-gate branch through negotiate()'s
    defaults, not a hand-built Proposal."""
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, total_amount=Decimal("900"))

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert state.discount_counters_issued == 1


def test_negotiate_capacity_only_returns_next_offer_and_sets_capacity():
    """The exact fix scenario: a bare "$200 today" answer becomes
    customer_capacity=200 with no total_amount/payments at all -- routes
    to request_next_offer and returns the $250 x 4 plan."""
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, customer_capacity=Decimal("200"))

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.payments == [Decimal("250.00")] * 4
    assert state.capacity == Decimal("200.00")


def test_negotiate_no_arguments_returns_next_offer_never_the_opening_anchor():
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    key = (verdict.counter_offer.tier, len(verdict.counter_offer.payments), verdict.counter_offer.cadence)
    assert key != neg._OPENING_ANCHOR_KEY


def test_negotiate_total_plus_capacity_overrides_the_derived_capacity():
    """An explicit customer_capacity beats whatever validate_proposal
    itself derives from the proposal (900/1 = 900) -- applied after the
    call returns, since validate_proposal always sets state.capacity from
    the proposal internally as its first step and can't be told not to
    (see negotiate()'s docstring, resolution step 3)."""
    state = _fresh_state()
    verdict = neg.negotiate(
        BALANCE, CALL_DATE, state, total_amount=Decimal("900"), number_of_payments=1,
        cadence=neg.Cadence.ONCE, customer_capacity=Decimal("850"),
    )

    assert verdict.decision == "COUNTER"
    assert state.capacity == Decimal("850.00")


def test_negotiate_payments_ignores_conflicting_number_of_payments():
    """The array is authoritative -- a conflicting number_of_payments=5
    alongside a 2-entry payments array must be ignored, not cause a
    count-mismatch rejection."""
    state = _fresh_state()
    verdict = neg.negotiate(
        BALANCE, CALL_DATE, state, payments=_decimals([300, 700]), number_of_payments=5,
    )

    assert verdict.decision == "ACCEPT"
    assert len(verdict.accepted_offer.payments) == 2


def test_negotiate_capacity_exhausted_then_no_agreement_not_a_rising_ladder():
    """Capacity supplied once, then repeated with the same figure (a
    consumer holding an unaffordable position): one offer, then
    NO_AGREEMENT -- never a second, larger offer volunteered instead."""
    state = _fresh_state()
    first = neg.negotiate(BALANCE, CALL_DATE, state, customer_capacity=Decimal("50"))
    assert first.decision == "COUNTER"

    second = neg.negotiate(BALANCE, CALL_DATE, state, customer_capacity=Decimal("50"))
    assert second.decision == "NO_AGREEMENT"


def test_negotiate_sum_mismatch_still_rejects_with_agent_note():
    """The live failure this whole merge exists to fix: total_amount=1000,
    payments=[200] reaches negotiate() exactly as an agent might send it
    (both fields explicitly present, disagreeing) -- step 1 does NOT
    derive total from payments here (total_amount is not absent), so the
    mismatch reaches validate_proposal's own sanity check unchanged."""
    state = _fresh_state()
    verdict = neg.negotiate(
        BALANCE, CALL_DATE, state, total_amount=Decimal("1000"), payments=_decimals([200]),
        number_of_payments=1, cadence=neg.Cadence.ONCE, first_payment_date=CALL_DATE,
    )

    assert verdict.decision == "COUNTER"
    assert verdict.agent_note == "payments sum to $200 but total_amount was $1000 -- they must match"


# --- discount_requested: an honest way to report "they asked for a
# reduction" when the customer named no figure at all, without inventing
# a total to make one up. ---
def test_negotiate_discount_requested_gate_locked_spends_and_counters_non_discount():
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier != neg.Tier.SETTLEMENT
    assert state.discount_counters_issued == 1
    # RE-BASELINED: an instruction to call the tool again, not an
    # explanation of the gate -- see Verdict.agent_note's docstring.
    assert verdict.agent_note == neg._GATE_LOCKED_AGENT_NOTE


def test_negotiate_discount_requested_never_returns_the_opening_anchor():
    """Excluded exactly like request_next_offer -- this may be the first
    tool call after the anchor was already spoken directly."""
    state = _fresh_state()
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

    key = (verdict.counter_offer.tier, len(verdict.counter_offer.payments), verdict.counter_offer.cadence)
    assert key != neg._OPENING_ANCHOR_KEY


def test_negotiate_discount_requested_gate_unlocked_offers_the_shallowest_step_first():
    """RE-BASELINED: the gate controls WHEN a discount becomes available,
    not HOW MUCH -- the first no-figure ask once unlocked used to jump
    straight to the maximum discount (the ceiling); it now offers the
    shallowest step (5% off at today's 20%-over-4-steps ladder), matching
    a customer who names an actual figure (capped at their own number,
    never handed more than asked for)."""
    state = _fresh_state()
    state.discount_counters_issued = neg.MAX_DISCOUNT_COUNTERS  # already unlocked

    verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

    assert verdict.decision == "COUNTER"
    assert verdict.counter_offer.tier == neg.Tier.SETTLEMENT
    assert verdict.counter_offer.total == Decimal("950.00")  # step 1 of 4: 5% off a $1000 balance
    assert verdict.counter_offer.payments == [Decimal("950.00")]
    assert verdict.agent_note is None
    assert state.settlement_steps_offered == 1


def test_negotiate_discount_requested_walks_steps_ascending_to_the_ceiling():
    """The full sequence: first ask (locked) spends the gate and counters
    with a non-discount option; each further no-figure ask (unlocked)
    walks one step deeper -- 5% -> 10% -> 15% -> 20%, then holds at the
    ceiling for any further asks rather than going deeper still."""
    state = _fresh_state()
    first = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)
    assert first.decision == "COUNTER"
    assert first.counter_offer.tier != neg.Tier.SETTLEMENT

    expected_totals = [Decimal("950.00"), Decimal("900.00"), Decimal("850.00"), Decimal("800.00")]
    for expected_total in expected_totals:
        verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)
        assert verdict.decision == "COUNTER"
        assert verdict.counter_offer.tier == neg.Tier.SETTLEMENT
        assert verdict.counter_offer.total == expected_total

    # A further ask holds at the ceiling -- never goes deeper than 20% off.
    held = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)
    assert held.counter_offer.total == Decimal("800.00")
    assert state.settlement_steps_offered == neg.SETTLEMENT_STEP_COUNT


def test_negotiate_discount_requested_step_splits_across_1_to_3_payments_by_capacity():
    """Each step is enumerated across 1-3 payments exactly like the
    ceiling, capacity-scored to the fewest that fit -- the exact figures
    from the live gap this fixes: step 1 (5% off a $1,000 balance) is
    $950 in one payment, $475 x2, or $316.67/$316.67/$316.66 across
    three, depending on what the customer can manage per payment."""
    expected = {
        None: (Decimal("950.00"), [Decimal("950.00")]),
        Decimal("950"): (Decimal("950.00"), [Decimal("950.00")]),
        Decimal("475"): (Decimal("950.00"), [Decimal("475.00"), Decimal("475.00")]),
        Decimal("316.67"): (
            Decimal("950.00"), [Decimal("316.67"), Decimal("316.67"), Decimal("316.66")],
        ),
    }
    for capacity, (total, payments) in expected.items():
        state = neg.NegotiationState(discount_counters_issued=neg.MAX_DISCOUNT_COUNTERS, capacity=capacity)
        verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

        assert verdict.counter_offer.tier == neg.Tier.SETTLEMENT
        assert verdict.counter_offer.total == total
        assert verdict.counter_offer.payments == payments


def test_negotiate_discount_requested_step_split_falls_back_to_most_split_when_still_unaffordable():
    """capacity=300 can't even reach the 3-payment split ($316.67 largest
    payment > $300) -- falls back to the most-split candidate (closest to
    affordable), same spirit as _select_counter's own unreachable
    fallback, rather than handing back something further over capacity
    than necessary."""
    state = neg.NegotiationState(discount_counters_issued=neg.MAX_DISCOUNT_COUNTERS, capacity=Decimal("300"))
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

    assert verdict.counter_offer.tier == neg.Tier.SETTLEMENT
    assert len(verdict.counter_offer.payments) == 3
    assert verdict.counter_offer.total == Decimal("950.00")


def test_negotiate_discount_requested_step_splits_never_touch_offered():
    """Same reasoning as the single-payment version -- every step, at
    every payment count, shares its (tier, n, cadence) key with the
    ceiling's own candidates; marking one "offered" would wrongly exhaust
    that shape for every other caller."""
    state = neg.NegotiationState(discount_counters_issued=neg.MAX_DISCOUNT_COUNTERS, capacity=Decimal("475"))
    verdict = neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)

    assert verdict.counter_offer.payments == [Decimal("475.00"), Decimal("475.00")]
    assert (neg.Tier.SETTLEMENT, 2, neg.Cadence.MONTHLY) not in state.offered


def test_negotiate_discount_requested_steps_never_return_a_payment_below_the_floor():
    """Direct check of the enumeration itself, at the shallowest step (the
    tightest case relative to the ceiling's own already-floor-clearing
    split): no candidate's per-payment amount may fall below the floor."""
    floor = neg._floor(BALANCE)
    for tier, n, cadence, total in neg._settlement_candidate_specs(Decimal("950.00")):
        payments = neg._split_payments(total, n)
        assert all(p >= floor for p in payments)


def test_negotiate_discount_requested_steps_never_touch_offered():
    """Every step shares the same (SETTLEMENT, 1, ONCE) key -- `offered`
    can't distinguish between them, so this path must never add to it
    (adding any one step would make _select_counter treat the whole
    tier/count/cadence as permanently exhausted for every other caller,
    including the customer's own later specific settlement proposal)."""
    state = _fresh_state()
    neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)  # locked -> spend
    neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)  # unlocked -> step 1

    assert (neg.Tier.SETTLEMENT, 1, neg.Cadence.ONCE) not in state.offered


def test_negotiate_customer_settlement_figure_still_works_after_steps_walked():
    """The whole reason steps don't touch `offered`: a customer who names
    their own specific, legal settlement figure after some no-figure
    walking must still get it ACCEPTed on its own merits, not blocked
    because the (SETTLEMENT, 1, ONCE) shape looks "already offered"."""
    state = _fresh_state()
    neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)  # locked -> spend
    neg.negotiate(BALANCE, CALL_DATE, state, discount_requested=True)  # unlocked -> step 1 ($950)

    verdict = neg.negotiate(BALANCE, CALL_DATE, state, total_amount=Decimal("900"), number_of_payments=1, cadence=neg.Cadence.ONCE)
    assert verdict.decision == "ACCEPT"
    assert verdict.accepted_offer.total == Decimal("900.00")


def test_negotiate_discount_requested_respects_customer_capacity():
    """capacity=300 rules out the $750/$250 down-payment-plus-one
    (largest payment $750 > $300) -- selection must fall through to a
    candidate that actually fits."""
    state = _fresh_state()
    verdict = neg.negotiate(
        BALANCE, CALL_DATE, state, discount_requested=True, customer_capacity=Decimal("300"),
    )

    assert verdict.decision == "COUNTER"
    assert max(verdict.counter_offer.payments) <= Decimal("300.00")
    assert state.capacity == Decimal("300.00")


def test_negotiate_discount_requested_ignored_when_a_real_proposal_is_given():
    """A real proposal already carries its own discount-ness
    (_classify_tier's total < balance check) -- discount_requested adds
    nothing and must not divert a genuine proposal into the no-figure
    path."""
    state = _fresh_state()
    verdict = neg.negotiate(
        BALANCE, CALL_DATE, state, total_amount=Decimal("1000"), discount_requested=True,
    )

    assert verdict.decision == "ACCEPT"
    assert state.discount_counters_issued == 0  # never touched -- this was never a discount ask


def test_negotiate_discount_requested_false_is_the_same_as_omitted():
    state_a = _fresh_state()
    state_b = _fresh_state()
    a = neg.negotiate(BALANCE, CALL_DATE, state_a, discount_requested=False)
    b = neg.negotiate(BALANCE, CALL_DATE, state_b)

    assert a.decision == b.decision == "COUNTER"
    assert a.counter_offer.tier == b.counter_offer.tier
    assert state_a.discount_counters_issued == state_b.discount_counters_issued == 0
