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
# something else needed fixing. All of these use a fresh (locked) state. ---
@pytest.mark.parametrize(
    "total,n,payments",
    [
        (700, 1, None),  # 30% off, no split at all
        (500, 2, None),  # 50% off
        (100, 1, None),  # 90% off
        (800, 3, ["500", "200", "100"]),  # legal discount ceiling, illegal split
    ],
)
def test_gate_intercepts_illegal_discount_before_repair(total, n, payments):
    proposal = _proposal(total, n, cadence=neg.Cadence.MONTHLY, payments=payments)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert verdict.violations == ["discount_gate_locked"]
    assert verdict.counter_offer.total == Decimal("1000.00")
    assert state.discount_counters_issued == 1


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
    proposal = _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY)
    state = _fresh_state()
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "COUNTER"
    assert "discount_gate_locked" in verdict.violations
    assert verdict.counter_offer.tier == neg.Tier.DOWNPAYMENT_PLUS_ONE
    assert verdict.counter_offer.payments == [Decimal("750.00"), Decimal("250.00")]
    assert state.discount_counters_issued == 1


def test_gate_consumer_holds_accepts():
    proposal = _proposal(900, 2, cadence=neg.Cadence.BIWEEKLY)
    state = neg.NegotiationState(discount_counters_issued=1)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, state)

    assert verdict.decision == "ACCEPT"


def test_gate_counter_preserves_consumers_cadence():
    """The tier governs money, the consumer governs rhythm: proposing
    monthly cadence gets countered a month apart, not forced to two weeks."""
    proposal = _proposal(900, 2, cadence=neg.Cadence.MONTHLY)
    verdict = neg.validate_proposal(BALANCE, proposal, CALL_DATE, _fresh_state())

    assert verdict.counter_offer.cadence == neg.Cadence.MONTHLY
    assert verdict.counter_offer.dates == [CALL_DATE, neg._add_calendar_months(CALL_DATE, 1)]


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
