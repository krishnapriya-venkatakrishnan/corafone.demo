"""app/tools.py: idempotency guards, correct dispatch, and the error-handling
path added in task 1 (a failed tool call must not crash the call and must
report a spoken-friendly error instead of hanging)."""

import json

from unittest.mock import AsyncMock, patch

from app import tools
from tests.conftest import make_function_call


async def test_settlement_charges_once_then_returns_cached_result(session):
    with patch("app.db.apply_settlement", new=AsyncMock()) as apply_settlement, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call("process_account_settlement", {"amount": 300.0}), session
        )
        await tools.handle_function_call_request(
            make_function_call("process_account_settlement", {"amount": 300.0}, "call_2"), session
        )

    assert apply_settlement.await_count == 1
    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["status"] == "success"
    assert responses[1]["status"] == "already_settled"
    assert responses[1]["transaction_id"] == responses[0]["transaction_id"]


async def test_payment_plan_created_once_then_returns_cached_result(session):
    with patch("app.db.create_payment_plan", new=AsyncMock()) as create_plan, \
         patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "offer_payment_plan",
                {"num_installments": 5, "amount_per_installment": 100.0, "start_date": "2026-07-10"},
            ),
            session,
        )
        await tools.handle_function_call_request(
            make_function_call(
                "offer_payment_plan",
                {"num_installments": 3, "amount_per_installment": 200.0, "start_date": "2026-08-01"},
                "call_2",
            ),
            session,
        )

    assert create_plan.await_count == 1
    responses = [
        json.loads(r.content) for r in session.agent_connection.sent_function_call_responses
    ]
    assert responses[0]["status"] == "plan_created"
    assert responses[1]["status"] == "already_created"
    assert responses[1]["num_installments"] == 5  # the first (cached) plan, not the second attempt


async def test_unknown_function_is_ignored_without_crashing(session):
    await tools.handle_function_call_request(make_function_call("not_a_real_tool", {}), session)

    assert session.agent_connection.sent_function_call_responses == []


async def test_malformed_date_is_caught_and_reported_as_error(session):
    with patch("app.db.create_payment_plan", new=AsyncMock()), patch("app.db.log_communication", new=AsyncMock()):
        await tools.handle_function_call_request(
            make_function_call(
                "offer_payment_plan",
                {"num_installments": 3, "amount_per_installment": 100.0, "start_date": "not a date"},
            ),
            session,
        )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "error"
    assert session.error_count == 1


async def test_db_failure_is_caught_and_reported_as_error(session):
    with patch("app.db.apply_settlement", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await tools.handle_function_call_request(
            make_function_call("process_account_settlement", {"amount": 300.0}), session
        )

    response = json.loads(session.agent_connection.sent_function_call_responses[0].content)
    assert response["status"] == "error"
    assert session.error_count == 1
    assert session.settlement_settled is False
