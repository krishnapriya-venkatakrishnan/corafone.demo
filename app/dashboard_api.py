"""Read-only dashboard API + the live scenario-test runner, both consumed by
the TypeScript dashboard (dashboard/). Deliberately the one place app/ code
imports from tests/ -- the scenario runner's entire purpose is exposing the
Layer 3 test suite (tests/scenarios/) as a product feature, reusing it
in-process rather than reimplementing it or shelling out to pytest."""

import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config, db, storage
from .session import CallSession

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# --- Response models ---
class AccountSummary(BaseModel):
    account_id: int
    customer_name: str
    phone_number: str
    current_balance: float
    status: str


class ComplianceSummary(BaseModel):
    total_calls: int
    mini_miranda_pass_rate: float | None
    avg_tone_score: float | None
    hallucination_count: int
    prohibited_conduct_count: int
    total_judge_cost_usd: float | None


class DashboardSummary(BaseModel):
    account: AccountSummary | None
    compliance: ComplianceSummary


class CallRecord(BaseModel):
    session_id: str
    account_id: int
    created_at: datetime | None
    total_duration_seconds: int
    avg_latency_ms: int
    barge_in_count: int
    disposition_code: str
    error_count: int
    transcript_path: str | None
    mini_miranda_passed: bool | None = None
    pii_redacted_correctly: bool | None = None
    hallucination_detected: bool | None = None
    identity_verified_before_disclosure: bool | None = None
    prohibited_conduct_detected: bool | None = None
    right_to_cease_honored: bool | None = None
    tone_score: int | None = None
    judge_reasoning: str | None = None
    judge_cost_usd: float | None = None


class PaymentPlanRecord(BaseModel):
    plan_id: int
    account_id: int
    num_installments: int
    amount_per_installment: float
    total_amount: float
    start_date: date
    status: str
    created_at: datetime


class ScheduledCallbackRecord(BaseModel):
    callback_id: int
    account_id: int
    callback_time: datetime
    status: str
    created_at: datetime


class Commitments(BaseModel):
    payment_plans: list[PaymentPlanRecord]
    scheduled_callbacks: list[ScheduledCallbackRecord]


class TranscriptResponse(BaseModel):
    session_id: str
    transcript: str


# --- Read-only endpoints ---
@router.get("/summary", response_model=DashboardSummary)
async def get_summary() -> DashboardSummary:
    account = await db.get_account(config.CUSTOMER_PHONE_NUMBER)
    compliance = await db.get_compliance_summary()
    return DashboardSummary(account=account, compliance=compliance)


@router.get("/calls", response_model=list[CallRecord])
async def get_calls() -> list[dict]:
    return await db.get_calls()


@router.get("/commitments", response_model=Commitments)
async def get_commitments() -> Commitments:
    payment_plans = await db.get_active_payment_plans()
    scheduled_callbacks = await db.get_pending_callbacks()
    return Commitments(payment_plans=payment_plans, scheduled_callbacks=scheduled_callbacks)


@router.get("/calls/{session_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(session_id: str) -> TranscriptResponse:
    path = await db.get_call_transcript_path(session_id)
    if not path:
        raise HTTPException(status_code=404, detail="No transcript found for this call.")
    text = await storage.download_call_log(path)
    return TranscriptResponse(session_id=session_id, transcript=text)


# --- Live scenario-test runner ---
async def _scenario_event_stream(trials: int):
    """Runs every scenario in tests/scenarios/definitions.py in-process,
    against a mocked DB (tests/mock_db.py) so this never touches the real
    demo account, yielding one SSE event per scenario as it finishes."""
    # Imported here, not at module load, to keep the tests/ dependency
    # visibly scoped to the one feature that needs it.
    from tests.conftest import FakeWebSocket
    from tests.mock_db import mocked_db
    from tests.scenarios import structural_checks
    from tests.scenarios.definitions import SCENARIOS
    from tests.scenarios.harness import run_conversation
    from tests.scenarios.judge import judge_scenario

    with mocked_db():
        for scenario in SCENARIOS:
            hard_failures: list[str] = []
            judge_passes = 0
            trial_details = []

            for trial in range(trials):
                session = CallSession(websocket=FakeWebSocket(), account_id=42)
                result = await run_conversation(scenario.consumer_persona, session)

                multi_sentence = structural_checks.one_sentence_per_turn(result.transcript)
                duplicate_calls = structural_checks.tool_called_at_most_once(result.tool_calls)
                if multi_sentence:
                    hard_failures.append(f"trial {trial}: multi-sentence turn(s)")
                if duplicate_calls:
                    hard_failures.append(f"trial {trial}: tool(s) called more than once")

                judgment = await judge_scenario(result.transcript, scenario.expected_outcome)
                if judgment.outcome_met:
                    judge_passes += 1

                trial_details.append(
                    {
                        "trial": trial,
                        "outcome_met": judgment.outcome_met,
                        "reasoning": judgment.reasoning,
                        "multi_sentence_violations": multi_sentence,
                        "duplicate_tool_calls": duplicate_calls,
                        "transcript": result.transcript,
                    }
                )

            event = {
                "type": "scenario_result",
                "scenario": scenario.name,
                "expected_outcome": scenario.expected_outcome,
                "trials": trials,
                "judge_passes": judge_passes,
                "hard_failures": hard_failures,
                "trial_details": trial_details,
            }
            yield f"data: {json.dumps(event)}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.get("/scenarios/run")
async def run_scenarios(trials: int = 1) -> StreamingResponse:
    """GET (not POST) so the browser's native EventSource can consume it
    directly. `trials` defaults to 1 for a quick live demo -- bump it for a
    more statistically meaningful pass-rate read, same as the pytest suite's
    N_TRIALS=3."""
    return StreamingResponse(_scenario_event_stream(trials), media_type="text/event-stream")
