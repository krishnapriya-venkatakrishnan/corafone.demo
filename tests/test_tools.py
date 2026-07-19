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

from app import tools
from tests.conftest import make_function_call


def _iso(d):
    return d.isoformat()


TODAY = date.today()


async def test_validate_proposal_accepts_full_balance(session):
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
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
            "validate_consumer_proposal",
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

    RE-BASELINED: $400 (was used here) no longer spends the gate on a $500
    balance -- capacity $400 still reaches the down-payment-plus-one tier
    ($375 leading payment), which beats settlement in tier order regardless
    of whether settlement is excluded, so excluding it changes nothing (see
    negotiation.py's capacity-scored gate fix). $300 genuinely can't reach
    that tier ($375 > $300), so excluding settlement does change the
    outcome there, and the gate is spent -- this test's actual subject
    (turn-scoped caching) is unaffected by which amount is used."""
    discount_proposal = {
        "total_amount": 300.0,  # a discount request that genuinely spends the gate on a $500 balance
        "number_of_payments": 1,
        "cadence": "once",
        "first_payment_date": _iso(TODAY),
    }

    await tools.handle_function_call_request(
        make_function_call("validate_consumer_proposal", discount_proposal), session
    )
    await tools.handle_function_call_request(
        make_function_call("validate_consumer_proposal", discount_proposal, "call_2"), session
    )

    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0] == responses[1]
    assert session.negotiation_state.discount_counters_issued == 1


async def test_validate_proposal_caches_across_int_float_representations(session):
    """The cache key is built from the converted values (Decimal/int), not
    the raw JSON -- 300 and 300.0 must be treated as the same proposal.
    RE-BASELINED to $300 -- see test_validate_proposal_caches_within_same_turn
    for why $400 no longer spends the gate on this $500-balance session."""
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
            {"total_amount": 300, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
        ),
        session,
    )
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
            {"total_amount": 300.0, "number_of_payments": 1.0, "cadence": "once", "first_payment_date": _iso(TODAY)},
            "call_2",
        ),
        session,
    )

    assert session.negotiation_state.discount_counters_issued == 1


async def test_validate_proposal_revalidates_on_new_turn(session):
    """A genuinely new turn re-runs validation -- if the consumer holds
    their position after the gate has already been spent, the second,
    later call accepts.

    RE-BASELINED to $400 over 2 monthly payments (was $400 as a single
    payment). A single $400 payment no longer spends the gate at all on
    this $500 balance (see test_validate_proposal_caches_within_same_turn),
    and a total low enough to genuinely spend the gate as a single payment
    (e.g. $300) is below the $400 settlement ceiling, so it can never
    legally ACCEPT even once unlocked -- it only ever repairs up to $400.
    Splitting the same $400 across 2 payments decouples the two
    requirements: capacity is $400/2 = $200 (low enough that excluding
    settlement genuinely changes the outcome, so the gate is spent), while
    the total itself sits exactly at the settlement ceiling, which is
    legal and must ACCEPT once the gate is unlocked and the consumer holds
    their position."""
    discount_proposal = {
        "total_amount": 400.0,
        "number_of_payments": 2,
        "cadence": "monthly",
        "first_payment_date": _iso(TODAY),
    }

    await tools.handle_function_call_request(
        make_function_call("validate_consumer_proposal", discount_proposal), session
    )
    session.turn_id += 1  # a new customer utterance started a new turn
    await tools.handle_function_call_request(
        make_function_call("validate_consumer_proposal", discount_proposal, "call_2"), session
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
    -- the consumer never repeated themselves. RE-BASELINED to $300/$350 --
    see test_validate_proposal_caches_within_same_turn for why $400 no
    longer spends the gate on this $500-balance session; $300 and $350
    both still do (each is below the $375 down-payment-plus-one leading
    payment that would otherwise make excluding settlement moot)."""
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
            {"total_amount": 300.0, "number_of_payments": 1, "cadence": "once", "first_payment_date": _iso(TODAY)},
        ),
        session,
    )
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
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
    assert session.agreement_disposition == "PAYMENT_PLAN_ACTIVE"


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
    validate_consumer_proposal (and a *successful* record_agreement) may
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


async def test_unknown_function_is_ignored_without_crashing(session):
    await tools.handle_function_call_request(make_function_call("not_a_real_tool", {}), session)

    assert session.agent_connection.sent_function_call_responses == []


async def test_malformed_first_payment_date_counters_gracefully(session):
    """Unparseable input no longer crashes into the generic error path --
    app/negotiation.py's own hostile-input handling returns a normal
    COUNTER verdict instead."""
    await tools.handle_function_call_request(
        make_function_call(
            "validate_consumer_proposal",
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
            SimpleNamespace(name="validate_consumer_proposal", arguments="{not valid json", id="call_1")
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
