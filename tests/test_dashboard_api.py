"""app/dashboard_api.py: the read-only endpoints, via FastAPI's TestClient
with the DB mocked. The scenario-runner endpoint (/scenarios/run) isn't
covered here -- it makes real OpenAI calls, same as tests/scenarios/, so
it's out of scope for the free/fast Layer 1 suite. /queue/next's own
agentic decision (app/queue_agent.py) is unit-tested separately in
test_queue_agent.py -- here it's mocked, same as storage.download_call_log
is for the transcript endpoint below."""

from datetime import date, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import queue_agent
from app.main import app

client = TestClient(app)


def test_accounts_returns_list(patched_db_pool):
    patched_db_pool.fetch.return_value = [
        {"account_id": 1, "customer_name": "Marcus Vance", "phone_number": "+15550199",
         "current_balance": 500.0, "status": "ACTIVE", "requires_manual_review": False},
        {"account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
         "current_balance": 1450.0, "status": "ACTIVE", "requires_manual_review": False},
    ]

    response = client.get("/api/dashboard/accounts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[1]["customer_name"] == "Dana Whitfield"


def test_summary_with_account_id_uses_get_account_by_id(patched_db_pool):
    patched_db_pool.fetchrow.side_effect = [
        {
            "account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
            "current_balance": 1450.0, "status": "ACTIVE", "requires_manual_review": False,
        },
        {
            "total_calls": 1, "mini_miranda_pass_rate": 1.0, "avg_tone_score": 5.0,
            "hallucination_count": 0, "prohibited_conduct_count": 0, "total_judge_cost_usd": 0.005,
        },
    ]

    response = client.get("/api/dashboard/summary?account_id=2")

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["customer_name"] == "Dana Whitfield"
    query = patched_db_pool.fetchrow.call_args_list[0].args[0]
    assert "WHERE account_id = $1" in query


def test_summary_returns_account_and_compliance(patched_db_pool):
    patched_db_pool.fetchrow.side_effect = [
        {
            "account_id": 42, "customer_name": "Marcus Vance", "phone_number": "+15550199",
            "current_balance": 500.0, "status": "ACTIVE", "requires_manual_review": False,
        },
        {
            "total_calls": 2, "mini_miranda_pass_rate": 1.0, "avg_tone_score": 4.5,
            "hallucination_count": 0, "prohibited_conduct_count": 0, "total_judge_cost_usd": 0.01,
        },
    ]

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["customer_name"] == "Marcus Vance"
    assert body["compliance"]["total_calls"] == 2


def test_summary_handles_no_account(patched_db_pool):
    patched_db_pool.fetchrow.side_effect = [
        None,
        {
            "total_calls": 0, "mini_miranda_pass_rate": None, "avg_tone_score": None,
            "hallucination_count": 0, "prohibited_conduct_count": 0, "total_judge_cost_usd": None,
        },
    ]

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["account"] is None


def test_calls_returns_joined_rows(patched_db_pool):
    patched_db_pool.fetch.return_value = [
        {
            "session_id": "sess_1", "account_id": 42, "created_at": "2026-07-04T10:00:00",
            "total_duration_seconds": 90, "avg_latency_ms": 850, "barge_in_count": 1,
            "disposition_code": "SETTLED", "error_count": 0, "transcript_path": "42/foo/log.txt",
            "mini_miranda_passed": True, "pii_redacted_correctly": True, "hallucination_detected": False,
            "identity_verified_before_disclosure": True, "prohibited_conduct_detected": False,
            "right_to_cease_honored": None, "tone_score": 5, "judge_reasoning": "Good call.",
            "judge_cost_usd": 0.004,
        }
    ]

    response = client.get("/api/dashboard/calls")

    assert response.status_code == 200
    assert response.json()[0]["session_id"] == "sess_1"


def test_commitments_returns_plans_and_callbacks(patched_db_pool):
    patched_db_pool.fetch.side_effect = [
        [
            {
                "plan_id": 1, "account_id": 42, "num_installments": 5, "amount_per_installment": 100.0,
                "total_amount": 500.0, "start_date": "2026-07-10", "status": "ACTIVE",
                "created_at": "2026-07-04T10:00:00",
            }
        ],
        [
            {
                "callback_id": 1, "account_id": 42, "callback_time": "2026-07-07T15:00:00",
                "status": "PENDING", "created_at": "2026-07-04T10:00:00",
            }
        ],
    ]

    response = client.get("/api/dashboard/commitments")

    assert response.status_code == 200
    body = response.json()
    assert body["payment_plans"][0]["plan_id"] == 1
    assert body["scheduled_callbacks"][0]["callback_id"] == 1


def test_transcript_404_when_no_path_on_record(patched_db_pool):
    patched_db_pool.fetchrow.return_value = None

    response = client.get("/api/dashboard/calls/sess_missing/transcript")

    assert response.status_code == 404


def test_transcript_returns_text(patched_db_pool, monkeypatch):
    patched_db_pool.fetchrow.return_value = {"transcript_path": "42/foo/log.txt"}

    async def fake_download(path):
        assert path == "42/foo/log.txt"
        return "assistant: hi\nuser: hello"

    monkeypatch.setattr("app.dashboard_api.storage.download_call_log", fake_download)

    response = client.get("/api/dashboard/calls/sess_1/transcript")

    assert response.status_code == 200
    assert response.json()["transcript"] == "assistant: hi\nuser: hello"


def test_queue_next_returns_recommendation(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.side_effect = [
        [
            {"account_id": 1, "customer_name": "Marcus Vance", "phone_number": "+15550199",
             "current_balance": 0.0, "status": "SETTLED", "requires_manual_review": False},
            {"account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
             "current_balance": 1450.0, "status": "ACTIVE", "requires_manual_review": False},
            {"account_id": 3, "customer_name": "Miguel Ortiz", "phone_number": "+15550103",
             "current_balance": 275.0, "status": "ACTIVE", "requires_manual_review": False},
        ],
        [],  # get_pending_callbacks(2)
        [],  # get_active_payment_plans(2)
        [],  # get_calls(2)
        [],  # get_pending_callbacks(3)
        [],  # get_active_payment_plans(3)
        [],  # get_calls(3)
    ]

    async def fake_decide(candidates):
        assert [c["account_id"] for c in candidates] == [2, 3]  # account 1 excluded (SETTLED)
        return queue_agent.QueueDecision(account_id=3, reasoning="No history on either; picked one.")

    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", fake_decide)

    response = client.get("/api/dashboard/queue/next")

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["customer_name"] == "Miguel Ortiz"
    assert body["candidates_considered"] == 2


def test_queue_next_returns_none_when_no_eligible_accounts(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.return_value = [
        {"account_id": 1, "customer_name": "Marcus Vance", "phone_number": "+15550199",
         "current_balance": 0.0, "status": "SETTLED", "requires_manual_review": False},
    ]
    decide_mock = AsyncMock()
    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", decide_mock)

    response = client.get("/api/dashboard/queue/next")

    assert response.status_code == 200
    body = response.json()
    assert body["account"] is None
    assert body["candidates_considered"] == 0
    decide_mock.assert_not_called()


def test_queue_next_respects_exclude_ids(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.side_effect = [
        [
            {"account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
             "current_balance": 1450.0, "status": "ACTIVE", "requires_manual_review": False},
            {"account_id": 3, "customer_name": "Miguel Ortiz", "phone_number": "+15550103",
             "current_balance": 275.0, "status": "ACTIVE", "requires_manual_review": False},
        ],
        [],  # get_pending_callbacks(3) -- account 2 excluded via ?exclude_ids=2
        [],  # get_active_payment_plans(3)
        [],  # get_calls(3)
    ]

    async def fake_decide(candidates):
        assert [c["account_id"] for c in candidates] == [3]
        return queue_agent.QueueDecision(account_id=3, reasoning="Only candidate left.")

    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", fake_decide)

    response = client.get("/api/dashboard/queue/next?exclude_ids=2")

    assert response.status_code == 200
    assert response.json()["account"]["account_id"] == 3


def test_queue_next_excludes_accounts_requiring_manual_review(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.side_effect = [
        [
            {"account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
             "current_balance": 1450.0, "status": "ACTIVE", "requires_manual_review": True},
            {"account_id": 3, "customer_name": "Miguel Ortiz", "phone_number": "+15550103",
             "current_balance": 275.0, "status": "ACTIVE", "requires_manual_review": False},
        ],
        [],  # get_pending_callbacks(3) -- account 2 never even reaches this stage
        [],  # get_active_payment_plans(3)
        [],  # get_calls(3)
    ]

    async def fake_decide(candidates):
        assert [c["account_id"] for c in candidates] == [3]
        return queue_agent.QueueDecision(account_id=3, reasoning="Only candidate not flagged for review.")

    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", fake_decide)

    response = client.get("/api/dashboard/queue/next")

    assert response.status_code == 200
    assert response.json()["account"]["account_id"] == 3


def test_queue_next_excludes_account_with_future_callback(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.side_effect = [
        [
            {"account_id": 4, "customer_name": "Chandler Bing", "phone_number": "+15550204",
             "current_balance": 500.0, "status": "ACTIVE", "requires_manual_review": False},
        ],
        [{"callback_id": 1, "account_id": 4, "callback_time": datetime(2099, 1, 1), "status": "PENDING",
          "created_at": datetime(2026, 7, 1)}],  # get_pending_callbacks(4) -- far future, not due yet
        [],  # get_active_payment_plans(4)
    ]
    decide_mock = AsyncMock()
    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", decide_mock)

    response = client.get("/api/dashboard/queue/next")

    assert response.status_code == 200
    body = response.json()
    assert body["account"] is None
    decide_mock.assert_not_called()


def test_queue_next_excludes_account_with_future_payment_due_date(patched_db_pool, monkeypatch):
    patched_db_pool.fetch.side_effect = [
        [
            {"account_id": 3, "customer_name": "Joey Tribbiani", "phone_number": "+15550203",
             "current_balance": 500.0, "status": "PAYMENT_PLAN_ACTIVE", "requires_manual_review": False},
        ],
        [],  # get_pending_callbacks(3)
        [{"plan_id": 1, "account_id": 3, "num_installments": 3, "amount_per_installment": 166.67,
          "total_amount": 500.0, "start_date": date(2099, 1, 1), "status": "ACTIVE",
          "created_at": datetime(2026, 7, 1)}],  # get_active_payment_plans(3) -- far future
    ]
    decide_mock = AsyncMock()
    monkeypatch.setattr("app.dashboard_api.queue_agent.decide_next_call", decide_mock)

    response = client.get("/api/dashboard/queue/next")

    assert response.status_code == 200
    body = response.json()
    assert body["account"] is None
    decide_mock.assert_not_called()


def test_list_scenarios_returns_catalog():
    response = client.get("/api/dashboard/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert all("name" in s and "expected_outcome" in s for s in body)


def test_run_one_scenario_404s_for_unknown_name():
    response = client.get("/api/dashboard/scenarios/run/not_a_real_scenario")

    assert response.status_code == 404
