"""app/tools.py: the boundary between the model's raw tool arguments and
app/negotiation.py's typed API -- turn memoization, the concession-gate
shopping guard, the record_agreement idempotency guard and its
server-side re-validation (against a copy of negotiation_state, so a
refused write can't spend the gate), and the error-handling path (a
failed tool call, including malformed arguments, must still get a
function-call response instead of hanging the call)."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from unittest.mock import AsyncMock, patch

from app import negotiation as neg
from app import tools
from tests.conftest import make_function_call


def _iso(d):
    return d.isoformat()


TODAY = date.today()


async def test_validate_proposal_accepts_full_balance(session):
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 500.0,
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": _iso(TODAY),
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "ACCEPT"
    assert response["offer"]["total"] == "500.00"
    assert "violations" not in response


async def test_validate_proposal_never_exposes_violations(session):
    """Even on a COUNTER, the machine-readable violation codes never reach
    the JSON handed back to the model -- see Verdict's docstring."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 100.0,  # 80% off -- illegal discount depth
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": _iso(TODAY),
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert "violations" not in response
    assert "reason" in response and response["reason"]


async def test_validate_proposal_caches_within_same_turn(session):
    """A duplicate tool call inside one reasoning turn must not re-spend the
    concession gate. Two identical calls, no turn boundary between them,
    must increment the gate counter exactly once.

    RE-BASELINED: selection now ranks by total collected first, tier order
    only as a tie-break (see negotiation.py's capacity-scored fix), so
    PAYMENT_PLAN n=4 ($125 leading payment, the lowest floor of any
    full-balance tier on this $500 balance) is reachable, fresh, and worth
    more than any settlement candidate at essentially any capacity that
    could name a bare total -- excluding T3 stops changing what
    _select_counter picks at all. $300/$350 (the previous values here) no
    longer spend the gate for that reason. What still spends it is the
    *other* clause: a total that is itself already a legal settlement (at
    or above the 80% floor), which ACCEPTs the instant the gate unlocks --
    $400 is exactly this $500 balance's ceiling. This test's actual subject
    (turn-scoped caching) is unaffected by which amount is used, as long as
    it genuinely spends the gate."""
    discount_proposal = {
        "total_amount": 400.0,  # the settlement ceiling on this $500 balance -- spends the gate on its own legality
        "number_of_payments": 1,
        "cadence": "once",
        "first_payment_date": _iso(TODAY),
    }

    await tools.handle_function_call_request(
        make_function_call("negotiate", discount_proposal), session
    )
    await tools.handle_function_call_request(
        make_function_call("negotiate", discount_proposal, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] == responses[1]
    assert session.negotiation_state.discount_counters_issued == 1


async def test_validate_proposal_caches_across_int_float_representations(session):
    """The cache key is built from the converted values (Decimal/int), not
    the raw JSON -- 400 and 400.0 must be treated as the same proposal.
    RE-BASELINED to $400 -- see test_validate_proposal_caches_within_same_turn
    for why $300 no longer spends the gate on this $500-balance session."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 400, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
        ),
        session,
    )
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 400.0, "number_of_payments": 1.0, "cadence": "once", "first_payment_date": _iso(TODAY)},
            "call_2",
        ),
        session,
    )

    assert session.negotiation_state.discount_counters_issued == 1


async def test_validate_proposal_revalidates_on_new_turn(session):
    """A genuinely new turn re-runs validation -- if the consumer holds
    their position after the gate has already been spent, the second,
    later call accepts.

    $400 (the settlement ceiling on this $500 balance) spends the gate on
    its own legality -- it's a total that would ACCEPT the instant the gate
    unlocks (see test_validate_proposal_caches_within_same_turn for why a
    below-ceiling total like $300 no longer spends the gate at all under
    the capacity-scored fix). Split over 2 monthly payments rather than
    one only to also exercise a multi-payment settlement's floor ($200 a
    payment, comfortably above the $125 floor) on the way to ACCEPT."""
    discount_proposal = {
        "total_amount": 400.0,
        "number_of_payments": 2,
        "cadence": "monthly",
        "first_payment_date": _iso(TODAY),
    }

    await tools.handle_function_call_request(
        make_function_call("negotiate", discount_proposal), session
    )
    session.turn_id += 1  # a new customer utterance started a new turn
    await tools.handle_function_call_request(
        make_function_call("negotiate", discount_proposal, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["decision"] == "COUNTER"
    assert responses[1]["decision"] == "ACCEPT"


async def test_gate_cannot_be_shopped_within_one_turn(session):
    """The model tries two *different* discount amounts in the same turn
    (no new customer input between them). The first spends the gate; the
    second must not benefit from negotiation_state now reading "unlocked"
    -- the consumer never repeated themselves.

    RE-BASELINED to $400/$350 -- see test_validate_proposal_caches_within_
    same_turn for why only a total at or above this $500 balance's $400
    settlement ceiling genuinely spends the gate under the capacity-scored
    fix (a below-ceiling total like $350 alone never would). $400 first,
    so the gate is actually spent this turn and the guard has something to
    intercept; $350 second, genuinely different from $400 so it isn't
    served from the same-proposal cache, but must still be answered from
    this turn's already-spent gate verdict rather than a fresh (now-
    unlocked) call."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 400.0, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
        ),
        session,
    )
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 350.0, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
            "call_2",
        ),
        session,
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["decision"] == "COUNTER"
    assert responses[1]["decision"] == "COUNTER"
    assert session.negotiation_state.discount_counters_issued == 1


# --- discount_requested: the honest way to report "they asked for a
# reduction" with no figure of their own. ---
async def test_negotiate_discount_requested_spends_the_gate_end_to_end(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"discount_requested": True}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert response["offer"]["tier"] != "settlement"
    assert session.negotiation_state.discount_counters_issued == 1
    # RE-BASELINED: an instruction to call the tool again, not an
    # explanation of the gate -- see app/negotiation.py's Verdict.agent_note.
    assert response["agent_note"] == neg._GATE_LOCKED_AGENT_NOTE


async def test_negotiate_discount_requested_second_ask_offers_ceiling_end_to_end(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"discount_requested": True}), session
    )
    session.turn_id += 1
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"discount_requested": True}, "call_2"), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[1].content)
    assert response["decision"] == "COUNTER"
    assert response["offer"]["tier"] == "settlement"
    assert "agent_note" not in response


async def test_negotiate_discount_requested_distinct_cache_key_from_bare_call(session):
    """Two calls in the same turn, one with discount_requested and one
    without, must not be treated as a duplicate of each other -- they're
    genuinely different requests."""
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"discount_requested": True}), session
    )
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] != responses[1]


async def test_gate_cannot_be_shopped_via_discount_requested_within_one_turn(session):
    """The same gate-shopping guard, exercised through discount_requested
    instead of a total_amount: a discount_requested ask followed by a
    genuinely different discount total in the same turn must not benefit
    from negotiation_state now reading "unlocked" from the first call."""
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"discount_requested": True}), session
    )
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 300.0, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
            "call_2",
        ),
        session,
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["decision"] == "COUNTER"
    assert responses[1]["decision"] == "COUNTER"
    assert responses[1]["offer"]["tier"] != "settlement"
    assert session.negotiation_state.discount_counters_issued == 1


async def test_record_agreement_settles_single_payment(session):
    """RE-BASELINED: a single payment now also writes a payment_plans row
    (section 9 -- one write path for every agreement, so a deferred lump
    sum still has its date recorded somewhere queryable), in addition to
    the settlement close. create_payment_plan must now be patched too."""
    with patch("app.db.apply_settlement", new=AsyncMock()) as apply_settlement, \
         patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "full_payment",
                    "total_amount": 500.0,
                    "number_of_payments": 1,
                    "cadence": "once",
                    "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    create_plan.assert_awaited_once()
    assert create_plan.call_args.args[1] == 1  # num_installments
    apply_settlement.assert_awaited_once_with(session.account_id)
    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "success"
    assert session.agreement_recorded is True
    assert session.agreement_disposition == "SETTLED"


async def test_record_agreement_creates_payment_plan_with_average_and_breakdown(session):
    """amount_per_installment is the average (so num_installments x
    amount_per_installment always equals total_amount, even for an uneven
    split); payments_breakdown carries the exact per-payment amounts."""
    with patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "payment_plan",
                    "total_amount": 500.0,
                    "number_of_payments": 3,
                    "cadence": "monthly",
                    "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    create_plan.assert_awaited_once()
    call_args = create_plan.call_args.args
    assert call_args[0] == session.account_id
    assert call_args[1] == 3  # num_installments
    # amount_per_installment x n is at most a cent off total_amount -- the
    # best any single "average" figure can do when the total doesn't
    # divide evenly by 3 -- vs. hundreds of dollars off using the first
    # (largest, front-loaded) payment instead.
    assert abs(call_args[2] * call_args[1] - call_args[3]) <= Decimal("0.01")
    assert call_args[5] == "166.67,166.67,166.66"  # payments_breakdown, the real split
    assert call_args[8] == session.session_id  # E2: session_id ties the plan to this call
    assert call_args[9] == "payment_plan"  # tier, for the Call Report (H)
    expected_dates = ",".join(
        _iso(neg._add_calendar_months(TODAY, i)) for i in range(3)
    )
    assert call_args[10] == expected_dates  # payment_dates
    assert session.agreement_disposition == "PAYMENT_PLAN_ACTIVE"


# --- E1: record_agreement persists the exact split a prior `negotiate`
# ACCEPTed, not a re-derived even one. ---
async def test_validate_proposal_accepts_explicit_uneven_split(session):
    """The wire schema's optional `payments` array lets the model convey a
    customer-stated uneven split (e.g. "$300 today, $200 in two weeks")
    instead of always implying an even one."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 500.0,
                "number_of_payments": 2,
                "cadence": "biweekly",
                "first_payment_date": _iso(TODAY),
                "payments": [300.0, 200.0],
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "ACCEPT"
    assert response["offer"]["payments"] == ["300.00", "200.00"]
    assert session.accepted_offer.payments == [Decimal("300.00"), Decimal("200.00")]


async def test_record_agreement_preserves_uneven_split_from_prior_accept(session):
    """The core E1 trace: `negotiate` ACCEPTs a customer-
    stated uneven split; record_agreement -- whose own wire schema carries
    no payments breakdown -- must persist that exact split, not silently
    normalise it to an even one nobody agreed to."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 500.0,
                "number_of_payments": 2,
                "cadence": "biweekly",
                "first_payment_date": _iso(TODAY),
                "payments": [300.0, 200.0],
            },
        ),
        session,
    )

    with patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "downpayment_plus_one",
                    "total_amount": 500.0,
                    "number_of_payments": 2,
                    "cadence": "biweekly",
                    "first_payment_date": _iso(TODAY),
                },
                "call_2",
            ),
            session,
        )

    call_args = create_plan.call_args.args
    assert call_args[5] == "300.00,200.00"  # payments_breakdown -- the agreed split, not 250/250
    response = json.loads(session.agent_connection.sent_function_call_responses[1].content)
    assert response["payments"] == ["300.00", "200.00"]


async def test_record_agreement_falls_back_to_even_split_when_terms_dont_match(session):
    """If the terms record_agreement re-validates don't match the last
    ACCEPT's shape (e.g. the model skipped straight to record_agreement
    with no prior `negotiate` ACCEPT this call), it must
    fall back to the freshly re-derived even split rather than trusting a
    stale or unrelated stored offer."""
    session.accepted_offer = None
    with patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "downpayment_plus_one",
                    "total_amount": 500.0,
                    "number_of_payments": 2,
                    "cadence": "biweekly",
                    "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    assert create_plan.call_args.args[5] == "250.00,250.00"


async def test_record_agreement_refuses_terms_that_do_not_validate(session):
    """Defense in depth: even if the model tries to record terms that were
    never accepted, record_agreement re-validates server-side and refuses.
    Unlocked state, so this exercises the discount-ceiling rejection itself,
    not the (separately tested) concession-gate lock."""
    session.negotiation_state.discount_counters_issued = 1
    with patch("app.db.apply_settlement", new=AsyncMock()) as apply_settlement, \
         patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan:
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "settlement",
                    "total_amount": 100.0,  # 80% off -- illegal regardless of the gate
                    "number_of_payments": 1,
                    "cadence": "once",
                    "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    apply_settlement.assert_not_called()
    create_plan.assert_not_called()
    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "rejected"
    assert "violations" not in response  # never surfaced, even on refusal
    assert session.agreement_recorded is False


async def test_record_agreement_rejection_does_not_spend_the_gate(session):
    """A rejected write must not mutate the live negotiation_state -- only
    `negotiate` (and a *successful* record_agreement) may
    advance the gate."""
    await tools.handle_function_call_request(
        make_function_call(
            "record_agreement",
            {
                "tier": "settlement",
                "total_amount": 400.0,  # a discount request, gate locked
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": _iso(TODAY),
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "rejected"
    assert session.negotiation_state.discount_counters_issued == 0
    assert session.agreement_recorded is False


async def test_record_agreement_only_once_per_call(session):
    with patch("app.db.apply_settlement", new=AsyncMock()) as apply_settlement, \
         patch("app.db.create_payment_plan", new=AsyncMock()), \
         patch("app.db.log_communication", new=AsyncMock()):
        agreement_args = {
            "tier": "full_payment",
            "total_amount": 500.0,
            "number_of_payments": 1,
            "cadence": "once",
            "first_payment_date": _iso(TODAY),
        }
        await tools.handle_function_call_request(
            make_function_call("record_agreement", agreement_args), session
        )
        await tools.handle_function_call_request(
            make_function_call("record_agreement", agreement_args, "call_2"), session
        )

    assert apply_settlement.await_count == 1
    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[1]["status"] == "already_recorded"


# --- negotiate, no-args/capacity-only dispatch -- the tool boundary for
# app/negotiation.py's request_next_offer, reached when negotiate() is
# called with no total_amount/payments (see negotiate()'s resolution
# order). Used when the customer names no figure of their own at all, or
# only a bare capacity figure. ---
async def test_negotiate_no_args_returns_a_counter_skipping_the_anchor(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert response["offer"]["tier"] != "full_payment"
    assert "violations" not in response


async def test_negotiate_no_args_caches_within_same_turn(session):
    """Two calls with no new customer utterance between them (no turn_id
    bump) must return the identical cached verdict, not consume a second
    candidate from the ladder."""
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] == responses[1]


async def test_negotiate_no_args_advances_on_new_turn(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )
    session.turn_id += 1  # a new customer utterance started a new turn
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["offer"] != responses[1]["offer"]


async def test_negotiate_no_args_sets_escalation_disposition_on_exhaustion(session):
    # Exhaust every non-settlement candidate directly against the session's
    # own negotiation_state so the very next negotiate() call has
    # nothing left to offer (gate stays locked -- settlement never enters
    # the pool either).
    balance = Decimal(str(session.account_balance))
    call_date = session.call_started_at.date()
    while True:
        verdict = neg.request_next_offer(balance, call_date, session.negotiation_state)
        if verdict.decision != "COUNTER":
            break

    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "NO_AGREEMENT"
    assert session.agreement_disposition == "ESCALATED_NO_AGREEMENT"


async def test_negotiate_no_args_logs_call_and_result(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert len(tool_lines) == 2
    assert "negotiate(" in tool_lines[0]
    assert "-> COUNTER:" in tool_lines[1]


# --- customer_capacity: the fix for the loop where the agent, with only a
# bare "$200 today" to go on, fabricated total_amount=1000, payments=[200]
# rather than a real proposal. ---
async def test_negotiate_customer_capacity_passes_through(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"customer_capacity": 100.0}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert session.negotiation_state.capacity == Decimal("100.00")


async def test_negotiate_caches_separately_per_customer_capacity(session):
    """Two calls in the same turn with different customer_capacity are
    different requests, not a duplicate -- must not return the first
    call's cached verdict for the second, differently-scoped one."""
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"customer_capacity": 100.0}), session
    )
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"customer_capacity": 450.0}, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] != responses[1]
    assert session.negotiation_state.capacity == Decimal("450.00")


async def test_negotiate_same_customer_capacity_still_caches_within_turn(session):
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"customer_capacity": 100.0}), session
    )
    await tools.handle_function_call_request(
        make_function_call("negotiate", {"customer_capacity": 100.0}, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] == responses[1]


# --- Verdict.agent_note / Verdict.offer_summary surfaced through the tool
# boundary -- both the JSON result the model reads and the [Tool]
# transcript line a human (or the structural checks) can read without
# replaying the call. ---
async def test_negotiate_no_args_routine_result_has_offer_summary_but_no_agent_note(session):
    """The routine COUNTER path -- a volunteered offer, nothing to
    correct -- carries offer_summary (there's an offer to confirm from
    later) but no agent_note (nothing needs the agent's attention)."""
    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert "agent_note" not in response
    assert response["offer_summary"]["payment_count"] == len(response["offer"]["payments"])


async def test_negotiate_no_args_no_agreement_result_has_no_agent_note(session):
    """RE-BASELINED (was ...has_agent_note, asserting the key was present):
    NO_AGREEMENT is terminal -- `reason` alone is spoken and complete, so
    agent_note is now omitted entirely, same as on ACCEPT -- see
    app/negotiation.py's Verdict.agent_note and _verdict_to_tool_result."""
    balance = Decimal(str(session.account_balance))
    call_date = session.call_started_at.date()
    while True:
        verdict = neg.request_next_offer(balance, call_date, session.negotiation_state)
        if verdict.decision != "COUNTER":
            break

    await tools.handle_function_call_request(
        make_function_call("negotiate", {}), session
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "NO_AGREEMENT"
    assert "agent_note" not in response


async def test_validate_proposal_malformed_call_gets_an_actionable_agent_note(session):
    """The exact failing call from the live loop: total_amount=1000,
    number_of_payments=1, payments=[200] -- previously repeated five times
    with no way for the agent to learn what was wrong."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 1000.0,
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": _iso(TODAY),
                "payments": [200.0],
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert response["agent_note"] == "payments sum to $200 but total_amount was $1000 -- they must match"

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert "[agent_note: payments sum to $200 but total_amount was $1000" in tool_lines[1]


async def test_validate_proposal_accept_has_offer_summary_and_no_agent_note(session):
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 500.0,
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": _iso(TODAY),
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "ACCEPT"
    assert "agent_note" not in response
    assert response["offer_summary"] == {
        "tier": "full_payment", "total": "$500", "payment_count": 1,
        "payments": ["$500"], "dates": [neg._speak_date(TODAY)],
    }


# --- G1: every tool call and its result is curated into the transcript
# (session.log_lines), so the compliance judge has ground truth for what
# was actually authorized, and a relay failure is visible without a manual
# replay. ---
async def test_validate_proposal_logs_call_and_result(session):
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {"total_amount": 500.0, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
        ),
        session,
    )

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert len(tool_lines) == 2
    assert "negotiate(total_amount=500.0" in tool_lines[0]
    assert "-> ACCEPT:" in tool_lines[1]


async def test_record_agreement_logs_call_and_result(session):
    with patch("app.db.apply_settlement", new=AsyncMock()), \
         patch("app.db.create_payment_plan", new=AsyncMock()), \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "full_payment", "total_amount": 500.0, "number_of_payments": 1,
                    "cadence": "once", "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert len(tool_lines) == 2
    assert "record_agreement(tier=full_payment" in tool_lines[0]
    assert "-> success:" in tool_lines[1]


async def test_malformed_json_still_logs_a_tool_result(session):
    message = SimpleNamespace(
        functions=[
            SimpleNamespace(name="negotiate", arguments="{not valid json", id="call_1")
        ]
    )
    await tools.handle_function_call_request(message, session)

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert len(tool_lines) == 2
    assert "unparseable arguments" in tool_lines[0]
    assert "-> error" in tool_lines[1]


async def test_unknown_function_gets_an_error_response_not_silence(session):
    """A name Deepgram sent that this module doesn't recognise must still
    get a function-call response -- silently dropping it (the old
    behaviour) leaves the Voice Agent waiting forever for a reply that
    never comes, stalling the call."""
    await tools.handle_function_call_request(make_function_call("not_a_real_tool", {}), session)

    responses = session.agent_connection.sent_function_call_responses
    assert len(responses) == 1
    assert responses[0].name == "not_a_real_tool"
    assert json.loads(responses[0].content)["status"] == "error"

    tool_lines = [line for line in session.log_lines if "[Tool]" in line]
    assert len(tool_lines) == 2
    assert "unrecognized function" in tool_lines[0]
    assert "-> error" in tool_lines[1]


async def test_malformed_first_payment_date_counters_gracefully(session):
    """Unparseable input no longer crashes into the generic error path --
    app/negotiation.py's own hostile-input handling returns a normal
    COUNTER verdict instead."""
    await tools.handle_function_call_request(
        make_function_call(
            "negotiate",
            {
                "total_amount": 500.0,
                "number_of_payments": 1,
                "cadence": "once",
                "first_payment_date": "not a date",
            },
        ),
        session,
    )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["decision"] == "COUNTER"
    assert session.error_count == 0


async def test_malformed_json_arguments_still_gets_a_response(session):
    """Regression: json.loads used to run outside the try block, so a
    malformed arguments string raised inside the fire-and-forget dispatch
    task with no response ever sent back to Deepgram -- the call would
    hang mid-conversation. It must be caught like any other tool failure."""
    message = SimpleNamespace(
        functions=[
            SimpleNamespace(name="negotiate", arguments="{not valid json", id="call_1")
        ]
    )

    await tools.handle_function_call_request(message, session)

    assert len(session.agent_connection.sent_function_call_responses) == 1
    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "error"
    assert session.error_count == 1


async def test_db_failure_during_record_agreement_is_caught_and_reported_as_error(session):
    with patch("app.db.apply_settlement", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await tools.handle_function_call_request(
            make_function_call(
                "record_agreement",
                {
                    "tier": "full_payment",
                    "total_amount": 500.0,
                    "number_of_payments": 1,
                    "cadence": "once",
                    "first_payment_date": _iso(TODAY),
                },
            ),
            session,
        )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "error"
    assert session.error_count == 1
    assert session.agreement_recorded is False
