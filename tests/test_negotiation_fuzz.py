"""Property-based fuzzing for app/negotiation.py: no LLM, no network, no DB --
generates random balances, proposals, and negotiation states, and asserts
the module's own invariants hold regardless. Every returned Offer already
self-validates via `_assert_clean` (an AssertionError here means a counter-
offer failed its own legality check -- exactly the D1 regression this guards
against: changing the window boundary in only one of its three sites), so
the bar this test adds on top is: never raises anything else, and payments
always sum exactly to the offer's total.

A fixed seed keeps this deterministic and fast enough to run in the default
suite (a few thousand cases); a much larger ad-hoc run (~75,000 cases) was
used to verify the D1/D2/D3 changes in this revision and found no failures."""

import random
from datetime import date, timedelta
from decimal import Decimal

from app import negotiation as neg

N_CASES = 3000
_SEED = 20260719

_CADENCES = list(neg.Cadence)


def _random_state(rng: random.Random) -> neg.NegotiationState:
    return neg.NegotiationState(
        discount_counters_issued=rng.choice([0, 1, 2]),
        date_counters_issued=rng.choice([0, 1, 2]),
        capacity=Decimal(rng.choice(["50", "200", "266.67", "500", "1000", "2000"])) if rng.random() < 0.7 else None,
        unreachable_offer_made=rng.random() < 0.2,
    )


def _random_proposal(rng: random.Random, call_date: date) -> neg.Proposal:
    total = Decimal(str(rng.choice([0, -100, 1, 50, 137.5, 200, 266.67, 500, 800, 900, 999.99, 1000, 1200, 2500])))
    n = rng.choice([1, 2, 3, 4, 5, 6, 24, 100])
    cadence = rng.choice(_CADENCES)
    first_date = call_date + timedelta(days=rng.choice([-30, -1, 0, 1, 7, 14, 15, 60, 72, 90, 91, 92, 93, 365]))
    payments = None
    if rng.random() < 0.3:
        # A random, possibly-invalid split -- may not sum to total, which
        # _is_sane must reject gracefully rather than raise on.
        payments = [Decimal(str(rng.choice([10, 50, 100, 250, 300, 333.33, 500]))) for _ in range(n)]
    return neg.Proposal(total=total, number_of_payments=n, cadence=cadence, first_payment_date=first_date, payments=payments)


def test_fuzz_never_raises_and_payments_always_sum_to_total():
    rng = random.Random(_SEED)
    call_date = date(2026, 7, 18)
    failures = []

    for i in range(N_CASES):
        balance = Decimal(str(rng.choice([137.50, 500, 800, 1000, 2500])))
        proposal = _random_proposal(rng, call_date)
        state = _random_state(rng)
        try:
            verdict = neg.validate_proposal(balance, proposal, call_date, state)
        except Exception as exc:  # pragma: no cover -- failures collected, not raised inline
            failures.append(f"case {i}: raised {exc!r} for balance={balance} proposal={proposal} state={state}")
            continue

        offer = verdict.accepted_offer or verdict.counter_offer
        if offer is not None and sum(offer.payments) != offer.total:
            failures.append(f"case {i}: payments {offer.payments} don't sum to total {offer.total}")

        # agent_note/offer_summary: non-None on every COUNTER (a correction
        # or an instruction to call again -- see Verdict's docstring),
        # falsy/None on ACCEPT and on NO_AGREEMENT (terminal -- `reason`
        # alone is complete; nothing to correct or call again for).
        if verdict.decision == "ACCEPT" or verdict.decision == "NO_AGREEMENT":
            if verdict.agent_note:
                failures.append(f"case {i}: {verdict.decision} has a non-empty agent_note {verdict.agent_note!r}")
        elif not verdict.agent_note:
            failures.append(f"case {i}: {verdict.decision} has no agent_note")
        if verdict.decision == "NO_AGREEMENT" and verdict.offer_summary is not None:
            failures.append(f"case {i}: NO_AGREEMENT has a non-None offer_summary")
        if offer is not None and verdict.offer_summary is None:
            failures.append(f"case {i}: a verdict with an offer has no offer_summary")

    assert not failures, "\n".join(failures[:20])


def test_fuzz_request_next_offer_never_raises_and_never_repeats():
    """Same invariants as validate_proposal's fuzz above, plus the one
    specific to this function: repeated calls against the same state must
    never return the same (tier, n, cadence) twice -- and, since request_
    next_offer is defined to never be the first thing offered in a call
    (see _OPENING_ANCHOR_KEY), must never return the opening anchor
    (full balance, one payment, today) either."""
    rng = random.Random(_SEED + 1)
    call_date = date(2026, 7, 18)
    failures = []

    for i in range(N_CASES // 3):
        balance = Decimal(str(rng.choice([137.50, 500, 800, 1000, 2500])))
        state = _random_state(rng)
        # customer_capacity fuzzed on the first call only, like a consumer
        # naming a bare figure once -- later calls in the loop reuse
        # whatever state.capacity that left behind, same as a real call.
        capacity = (
            Decimal(rng.choice(["50", "150", "200", "300", "450", "800", "1500"]))
            if rng.random() < 0.4 else None
        )
        seen = set()
        for call_n in range(rng.choice([1, 2, 5, 10])):
            try:
                verdict = neg.request_next_offer(
                    balance, call_date, state, customer_capacity=capacity if call_n == 0 else None,
                )
            except Exception as exc:  # pragma: no cover
                failures.append(f"case {i}: raised {exc!r} for balance={balance} state={state}")
                break

            if verdict.decision == "ACCEPT":  # pragma: no cover -- must never happen
                failures.append(f"case {i}: request_next_offer returned ACCEPT")

            offer = verdict.counter_offer
            if offer is None:
                # NO_AGREEMENT is terminal -- `reason` alone is spoken and
                # complete, nothing to correct or call again for, so
                # agent_note must be falsy here too (see Verdict's
                # docstring and _no_agreement_verdict).
                if verdict.agent_note:
                    failures.append(f"case {i}: NO_AGREEMENT has a non-empty agent_note {verdict.agent_note!r}")
                if verdict.offer_summary is not None:
                    failures.append(f"case {i}: NO_AGREEMENT has a non-None offer_summary")
                break
            # A routine volunteered offer -- nothing for the agent to
            # correct, so agent_note must be falsy (see negotiation.py's
            # request_next_offer: a note here would just teach the model
            # to skim the field).
            if verdict.agent_note:
                failures.append(f"case {i}: routine COUNTER has a non-empty agent_note {verdict.agent_note!r}")
            if verdict.offer_summary is None:
                failures.append(f"case {i}: COUNTER has no offer_summary")
            if sum(offer.payments) != offer.total:
                failures.append(f"case {i}: payments {offer.payments} don't sum to total {offer.total}")
            key = (offer.tier, len(offer.payments), offer.cadence)
            if key == neg._OPENING_ANCHOR_KEY:
                failures.append(f"case {i}: returned the opening anchor {key}")
            if key in seen:
                failures.append(f"case {i}: repeated candidate {key}")
            seen.add(key)

    assert not failures, "\n".join(failures[:20])


def _random_negotiate_kwargs(rng: random.Random, call_date: date) -> dict:
    """Randomly includes or omits each of negotiate()'s six arguments
    independently -- unlike _random_proposal (always a full proposal),
    this exercises every combination an agent might actually send,
    including the sparse ones (bare capacity, total-only, payments with a
    conflicting count) that motivated the merge in the first place."""
    kwargs = {}
    if rng.random() < 0.5:
        kwargs["total_amount"] = Decimal(str(rng.choice([0, -100, 1, 50, 137.5, 200, 500, 800, 900, 999.99, 1000, 1200])))
    if rng.random() < 0.4:
        n = rng.choice([1, 2, 3, 4, 5, 6, 24])
        kwargs["payments"] = [Decimal(str(rng.choice([10, 50, 100, 250, 300, 333.33, 500]))) for _ in range(n)]
    if rng.random() < 0.3:
        kwargs["customer_capacity"] = Decimal(str(rng.choice([50, 150, 200, 300, 450, 800, 1500])))
    if rng.random() < 0.5:
        kwargs["cadence"] = rng.choice(_CADENCES)
    if rng.random() < 0.5:
        kwargs["first_payment_date"] = call_date + timedelta(days=rng.choice([-30, -1, 0, 1, 7, 14, 15, 60, 90, 91, 92, 365]))
    if rng.random() < 0.3:
        kwargs["number_of_payments"] = rng.choice([1, 2, 3, 4, 5, 24, 100])
    if rng.random() < 0.3:
        kwargs["discount_requested"] = True
    return kwargs


def test_fuzz_negotiate_never_raises_and_stays_internally_consistent():
    """negotiate() is a thin dispatcher (see its docstring) -- this isn't
    re-testing validate_proposal/request_next_offer's own decisions
    (covered above and throughout test_negotiation.py), it's fuzzing the
    resolution step itself: whatever sparse or conflicting combination of
    arguments comes in, negotiate() must never raise, and whatever it
    returns must satisfy the same structural invariants every Verdict
    does regardless of which internal function actually produced it."""
    rng = random.Random(_SEED + 2)
    call_date = date(2026, 7, 18)
    failures = []

    for i in range(N_CASES):
        balance = Decimal(str(rng.choice([137.50, 500, 800, 1000, 2500])))
        state = _random_state(rng)
        kwargs = _random_negotiate_kwargs(rng, call_date)
        is_bare_discount_request = (
            kwargs.get("discount_requested") and "total_amount" not in kwargs and "payments" not in kwargs
        )
        was_locked_before = not state.discount_unlocked
        try:
            verdict = neg.negotiate(balance, call_date, state, **kwargs)
        except Exception as exc:  # pragma: no cover
            failures.append(f"case {i}: raised {exc!r} for balance={balance} state={state} kwargs={kwargs}")
            continue

        # discount_requested with no proposal: locked -> never settlement
        # (that's the whole point of spending the gate first); unlocked ->
        # exactly step 1 of the graduated walk (settlement_steps_offered
        # starts at 0 in every freshly-constructed _random_state, so one
        # call always lands on step 1, never the ceiling directly -- see
        # negotiate()'s docstring for why the ceiling is no longer handed
        # out on the very first no-figure ask once unlocked).
        if is_bare_discount_request and verdict.counter_offer is not None:
            step_1_total = neg._settlement_step_total(balance, 1)
            if was_locked_before and verdict.counter_offer.tier == neg.Tier.SETTLEMENT:
                failures.append(f"case {i}: discount_requested with gate locked returned a settlement offer")
            if not was_locked_before and (
                verdict.counter_offer.tier != neg.Tier.SETTLEMENT or verdict.counter_offer.total != step_1_total
            ):
                failures.append(f"case {i}: discount_requested with gate unlocked didn't return step 1 ({step_1_total})")
            if not was_locked_before and state.settlement_steps_offered != 1:
                failures.append(f"case {i}: settlement_steps_offered should be 1 after one unlocked ask")

        offer = verdict.accepted_offer or verdict.counter_offer
        if offer is not None and sum(offer.payments) != offer.total:
            failures.append(f"case {i}: payments {offer.payments} don't sum to total {offer.total}")
        if verdict.decision == "ACCEPT" and verdict.agent_note:
            failures.append(f"case {i}: ACCEPT has a non-empty agent_note {verdict.agent_note!r}")
        if verdict.decision == "NO_AGREEMENT" and verdict.offer_summary is not None:
            failures.append(f"case {i}: NO_AGREEMENT has a non-None offer_summary")
        if offer is not None and verdict.offer_summary is None:
            failures.append(f"case {i}: a verdict with an offer has no offer_summary")
        # payments given -> number_of_payments must always be len(payments)
        # in whatever offer comes back on ACCEPT, regardless of a
        # conflicting number_of_payments also supplied (step 2: the array
        # is authoritative).
        if kwargs.get("payments") is not None and verdict.decision == "ACCEPT":
            if len(verdict.accepted_offer.payments) != len(kwargs["payments"]):
                failures.append(f"case {i}: ACCEPT payment count didn't match the given payments array")

    assert not failures, "\n".join(failures[:20])
