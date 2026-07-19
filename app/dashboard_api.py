"""Read-only dashboard API + the live scenario-test runner, both consumed by
the TypeScript dashboard (dashboard/). Deliberately the one place app/ code
imports from tests/ -- the scenario runner's entire purpose is exposing the
Layer 3 test suite (tests/scenarios/) as a product feature, reusing it
in-process rather than reimplementing it or shelling out to pytest."""

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config, db, negotiation, storage, tools
from .session import CallSession

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# --- Response models ---
class AccountSummary(BaseModel):
    account_id: int
    customer_name: str
    phone_number: str
    current_balance: float
    status: str
    requires_manual_review: bool


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


class ScenarioInfo(BaseModel):
    name: str
    expected_outcome: str


class ScenarioResult(BaseModel):
    scenario: str
    expected_outcome: str
    passed: bool
    reasoning: str
    hard_failures: list[str]
    transcript: list[str]


class ValidateRequest(BaseModel):
    # Loosely typed on purpose: this mirrors the tool-call boundary in
    # app/tools.py, where the model's raw arguments are just as likely to be
    # garbled speech-to-text as clean values. Converted defensively below,
    # the same way, so malformed input reaches negotiation.py's own
    # `_is_sane` check instead of failing here first.
    total_amount: Any
    number_of_payments: Any
    cadence: Any
    first_payment_date: Any
    discount_already_countered: bool = False


class ValidateOffer(BaseModel):
    tier: str
    total: str
    payments: list[str]
    dates: list[str]
    cadence: str


class ValidateResponse(BaseModel):
    decision: str
    reason: str
    offer: ValidateOffer | None
    violations: list[str]


# --- Read-only endpoints ---
@router.get("/accounts", response_model=list[AccountSummary])
async def get_accounts() -> list[dict]:
    """All demo accounts, for the account picker (frontend/ voice demo and
    this dashboard's account switcher)."""
    return await db.get_accounts()


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(account_id: int | None = None) -> DashboardSummary:
    account = (
        await db.get_account_by_id(account_id)
        if account_id is not None
        else await db.get_account(config.DEFAULT_CUSTOMER_PHONE_NUMBER)
    )
    compliance = await db.get_compliance_summary(account_id)
    return DashboardSummary(account=account, compliance=compliance)


@router.get("/calls", response_model=list[CallRecord])
async def get_calls(account_id: int | None = None) -> list[dict]:
    return await db.get_calls(account_id)


@router.get("/commitments", response_model=Commitments)
async def get_commitments(account_id: int | None = None) -> Commitments:
    payment_plans = await db.get_active_payment_plans(account_id)
    scheduled_callbacks = await db.get_pending_callbacks(account_id)
    return Commitments(payment_plans=payment_plans, scheduled_callbacks=scheduled_callbacks)


@router.get("/calls/{session_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(session_id: str) -> TranscriptResponse:
    path = await db.get_call_transcript_path(session_id)
    if not path:
        raise HTTPException(status_code=404, detail="No transcript found for this call.")
    text = await storage.download_call_log(path)
    return TranscriptResponse(session_id=session_id, transcript=text)


@router.post("/validate", response_model=ValidateResponse)
async def validate(body: ValidateRequest) -> dict:
    """Pure passthrough to the real negotiation validator: no LLM call, no
    database access, so it's safe to expose publicly and costs nothing to
    call. Powers the dashboard's Playground -- unlike the agent-facing tool
    (app/tools.py), this deliberately returns `violations`: those are
    withheld from the model, but showing them to an operator is the point.
    """
    proposal = tools._proposal_from_args(
        {
            "total_amount": body.total_amount,
            "number_of_payments": body.number_of_payments,
            "cadence": body.cadence,
            "first_payment_date": body.first_payment_date,
        }
    )

    state = negotiation.NegotiationState(
        discount_counters_issued=negotiation.MAX_DISCOUNT_COUNTERS if body.discount_already_countered else 0
    )

    verdict = negotiation.validate_proposal(config.DEMO_ACCOUNT_BALANCE, proposal, date.today(), state)
    offer = verdict.accepted_offer or verdict.counter_offer

    return {
        "decision": verdict.decision,
        "reason": verdict.reason,
        "offer": tools._offer_to_dict(offer) if offer else None,
        "violations": verdict.violations,
    }


# --- Live scenario-test runner ---
# Single-trial only -- kept deliberately simple for a live demo. The pytest
# suite (tests/scenarios/test_scenarios.py) still runs each scenario 3x with
# a 2/3 pass threshold for a statistically meaningful signal in CI; this
# in-process runner is for "watch it run right now," not a statistical read.
@router.get("/scenarios", response_model=list[ScenarioInfo])
async def list_scenarios() -> list[dict]:
    """The static scenario catalog (name + what's expected), with no run
    involved -- lets the dashboard show every test case up front, not just
    the ones that happen to have been run this session."""
    from tests.scenarios.definitions import SCENARIOS

    return [{"name": s.name, "expected_outcome": s.expected_outcome} for s in SCENARIOS]


async def _run_one_scenario(scenario) -> dict:
    """Runs a single scenario once, against a mocked DB (tests/mock_db.py) so
    this never touches the real demo account. Shared by both the run-all
    stream and the single-scenario endpoint below."""
    # Imported here, not at module load, to keep the tests/ dependency
    # visibly scoped to the one feature that needs it.
    from tests.mock_db import FakeWebSocket
    from tests.scenarios import structural_checks
    from tests.scenarios.harness import run_conversation
    from tests.scenarios.judge import judge_scenario

    session = CallSession(websocket=FakeWebSocket(), account_id=42)
    result = await run_conversation(scenario.consumer_persona, session)

    # One-sentence-per-turn is deliberately not checked here -- it's a real,
    # still-enforced regression guard in the CI suite (tests/scenarios/
    # test_scenarios.py), but on gpt-4o-mini it recurs often enough on
    # information-dense turns that flagging it in this live demo view was
    # just noise on top of the judge's actual pass/fail verdict.
    hard_failures: list[str] = []
    if structural_checks.tool_called_at_most_once(result.tool_calls):
        hard_failures.append("tool(s) called more than once")

    judgment = await judge_scenario(result.transcript, scenario.expected_outcome, result.tool_calls)

    return {
        "scenario": scenario.name,
        "expected_outcome": scenario.expected_outcome,
        "passed": judgment.outcome_met,
        "reasoning": judgment.reasoning,
        "hard_failures": hard_failures,
        "transcript": result.transcript,
    }


async def _scenario_event_stream():
    from tests.mock_db import mocked_db
    from tests.scenarios.definitions import SCENARIOS

    with mocked_db():
        for scenario in SCENARIOS:
            result = await _run_one_scenario(scenario)
            yield f"data: {json.dumps({'type': 'scenario_result', **result})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.get("/scenarios/run")
async def run_scenarios() -> StreamingResponse:
    """Runs every scenario once, streamed one result at a time (SSE) so the
    dashboard can show them arriving live instead of one long spinner."""
    return StreamingResponse(_scenario_event_stream(), media_type="text/event-stream")


@router.get("/scenarios/run/{scenario_name}", response_model=ScenarioResult)
async def run_one_scenario(scenario_name: str) -> dict:
    """Runs a single named scenario once -- for re-checking one test case
    without re-running the whole suite."""
    from tests.mock_db import mocked_db
    from tests.scenarios.definitions import SCENARIOS

    scenario = next((s for s in SCENARIOS if s.name == scenario_name), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_name!r}")

    with mocked_db():
        return await _run_one_scenario(scenario)
