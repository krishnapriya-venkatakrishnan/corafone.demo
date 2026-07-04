"""app/audit.py: judge cost computation, field pass-through to the DB, and
the failure path (an audit failure must never propagate -- the call has
already ended by the time this runs)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import audit


def _fake_response(report: audit.EvaluationReport, prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=report))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


async def test_run_compliance_audit_computes_cost_and_writes_all_fields(session):
    session.log_lines = ["2026-01-01 00:00:00 [assistant] hi"]
    report = audit.EvaluationReport(
        mini_miranda_passed=True,
        pii_redacted_correctly=True,
        hallucination_detected=False,
        identity_verified_before_disclosure=True,
        prohibited_conduct_detected=False,
        right_to_cease_honored=None,
        tone_score=5,
        judge_reasoning="Solid call.",
    )
    response = _fake_response(report, prompt_tokens=1000, completion_tokens=200)

    with patch("app.audit.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)), \
         patch("app.audit.db.create_ai_evaluation_log", new=AsyncMock()) as create_log:
        await audit.run_compliance_audit(session)

    create_log.assert_awaited_once()
    args = create_log.call_args.args
    assert args[0] == session.session_id
    assert args[1:9] == (True, True, False, True, False, None, 5, "Solid call.")

    expected_cost = (1000 * 2.50 / 1_000_000) + (200 * 10.00 / 1_000_000)
    assert abs(args[9] - expected_cost) < 1e-9


async def test_run_compliance_audit_flags_account_on_stop_contact_request(session):
    session.log_lines = ["2026-01-01 00:00:00 [user] please stop calling me"]
    report = audit.EvaluationReport(
        mini_miranda_passed=True,
        pii_redacted_correctly=True,
        hallucination_detected=False,
        identity_verified_before_disclosure=True,
        prohibited_conduct_detected=False,
        right_to_cease_honored=True,
        tone_score=5,
        judge_reasoning="Complied immediately.",
    )
    response = _fake_response(report, prompt_tokens=100, completion_tokens=50)

    with patch("app.audit.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)), \
         patch("app.audit.db.create_ai_evaluation_log", new=AsyncMock()), \
         patch("app.audit.db.set_requires_manual_review", new=AsyncMock()) as set_review:
        await audit.run_compliance_audit(session)

    set_review.assert_awaited_once_with(session.account_id)


async def test_run_compliance_audit_does_not_flag_account_when_not_applicable(session):
    session.log_lines = ["2026-01-01 00:00:00 [assistant] hi"]
    report = audit.EvaluationReport(
        mini_miranda_passed=True,
        pii_redacted_correctly=True,
        hallucination_detected=False,
        identity_verified_before_disclosure=True,
        prohibited_conduct_detected=False,
        right_to_cease_honored=None,
        tone_score=5,
        judge_reasoning="Solid call.",
    )
    response = _fake_response(report, prompt_tokens=100, completion_tokens=50)

    with patch("app.audit.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)), \
         patch("app.audit.db.create_ai_evaluation_log", new=AsyncMock()), \
         patch("app.audit.db.set_requires_manual_review", new=AsyncMock()) as set_review:
        await audit.run_compliance_audit(session)

    set_review.assert_not_awaited()


async def test_run_compliance_audit_swallows_openai_failure(session):
    session.log_lines = ["some line"]

    with patch(
        "app.audit.openai_client.beta.chat.completions.parse",
        new=AsyncMock(side_effect=RuntimeError("openai down")),
    ), patch("app.audit.db.create_ai_evaluation_log", new=AsyncMock()) as create_log:
        await audit.run_compliance_audit(session)  # must not raise

    create_log.assert_not_awaited()


async def test_run_compliance_audit_swallows_db_failure(session):
    session.log_lines = ["some line"]
    report = audit.EvaluationReport(
        mini_miranda_passed=True,
        pii_redacted_correctly=True,
        hallucination_detected=False,
        identity_verified_before_disclosure=True,
        prohibited_conduct_detected=False,
        right_to_cease_honored=True,
        tone_score=4,
        judge_reasoning="Fine.",
    )
    response = _fake_response(report, prompt_tokens=500, completion_tokens=100)

    with patch("app.audit.openai_client.beta.chat.completions.parse", new=AsyncMock(return_value=response)), \
         patch("app.audit.db.create_ai_evaluation_log", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await audit.run_compliance_audit(session)  # must not raise
