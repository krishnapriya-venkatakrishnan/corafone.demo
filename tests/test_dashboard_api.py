"""app/dashboard_api.py: the read-only endpoints, via FastAPI's TestClient
with the DB mocked. The scenario-runner endpoint (/scenarios/run) isn't
covered here -- it makes real OpenAI calls, same as tests/scenarios/, so
it's out of scope for the free/fast Layer 1 suite."""

from fastapi.testclient import TestClient

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


def test_list_scenarios_returns_catalog():
    response = client.get("/api/dashboard/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert all("name" in s and "expected_outcome" in s for s in body)


def test_run_one_scenario_404s_for_unknown_name():
    response = client.get("/api/dashboard/scenarios/run/not_a_real_scenario")

    assert response.status_code == 404


def test_run_one_scenario_gives_the_session_a_real_account_balance(monkeypatch):
    """Regression: CallSession's account_balance defaults to None, and
    app/tools.py reads session.account_balance directly for every tool
    call -- Decimal(str(None)) raises InvalidOperation, which every tool
    call in a live scenario run hit silently (caught and reported back to
    the model as a generic "system issue" error) until this was fixed.
    Mocks run_conversation itself -- no real OpenAI calls -- and just
    inspects the CallSession it was handed."""
    from dataclasses import dataclass, field

    from tests.scenarios.harness import TEST_ACCOUNT_BALANCE

    captured_sessions = []

    @dataclass
    class _FakeResult:
        transcript: list = field(default_factory=list)
        tool_calls: list = field(default_factory=list)

    async def fake_run_conversation(consumer_persona, session, max_turns=10):
        captured_sessions.append(session)
        return _FakeResult()

    async def fake_judge_scenario(transcript, expected_outcome, tool_calls=None):
        from tests.scenarios.judge import ScenarioJudgment
        return ScenarioJudgment(outcome_met=True, reasoning="stub")

    monkeypatch.setattr("tests.scenarios.harness.run_conversation", fake_run_conversation)
    monkeypatch.setattr("tests.scenarios.judge.judge_scenario", fake_judge_scenario)

    from tests.mock_db import mocked_db

    with mocked_db():
        response = client.get("/api/dashboard/scenarios/run/happy_path_full_payment")

    assert response.status_code == 200
    assert len(captured_sessions) == 1
    assert captured_sessions[0].account_balance == TEST_ACCOUNT_BALANCE


def test_run_one_scenario_reports_infrastructure_failure_as_crashed_not_failed(monkeypatch):
    """An OpenAI API failure (rate limit, timeout, network error) mid-run
    must never be scored as a compliance failure -- it never produced a
    verdict at all. crashed=True, passed=False (so a caller that only
    checks `passed` still fails closed), and the run-all stream must not
    propagate the exception (this endpoint call itself must still 200)."""
    async def fake_run_conversation_that_rate_limits(consumer_persona, session, max_turns=10):
        raise RuntimeError("Rate limit reached for gpt-4o-mini ... (simulated)")

    monkeypatch.setattr(
        "tests.scenarios.harness.run_conversation", fake_run_conversation_that_rate_limits
    )

    from tests.mock_db import mocked_db

    with mocked_db():
        response = client.get("/api/dashboard/scenarios/run/happy_path_full_payment")

    assert response.status_code == 200
    body = response.json()
    assert body["crashed"] is True
    assert body["passed"] is False
    assert "Rate limit" in body["error"]
    assert body["hard_failures"] == []
    assert body["transcript"] == []
