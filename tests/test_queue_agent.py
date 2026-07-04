"""app/queue_agent.py: verifies the candidate list is handed to the model
and the parsed decision is returned as-is -- no business logic lives here,
that's app/dashboard_api.py's job (see test_dashboard_api.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import queue_agent


def _fake_response(decision: queue_agent.QueueDecision):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=decision))])


async def test_decide_next_call_returns_parsed_decision():
    decision = queue_agent.QueueDecision(account_id=2, reasoning="Longest since last call.")
    response = _fake_response(decision)
    candidates = [{"account_id": 2, "customer_name": "Dana Whitfield"}]

    with patch(
        "app.queue_agent.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)
    ) as parse:
        result = await queue_agent.decide_next_call(candidates)

    assert result is decision
    parse.assert_awaited_once()
    kwargs = parse.call_args.kwargs
    assert str(candidates) in kwargs["messages"][1]["content"]


async def test_decide_next_call_can_return_no_account():
    decision = queue_agent.QueueDecision(account_id=None, reasoning="All candidates asked to stop contact.")
    response = _fake_response(decision)

    with patch(
        "app.queue_agent.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)
    ):
        result = await queue_agent.decide_next_call([])

    assert result.account_id is None
