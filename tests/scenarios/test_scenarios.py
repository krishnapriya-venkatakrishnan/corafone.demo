"""Layer 3 entry point: one test per scenario in definitions.py. Excluded
from the default `pytest` run (see pytest.ini) -- run explicitly via
`pytest -m scenario`. Costs real OpenAI tokens (Collector + Consumer +
judge calls, x N_TRIALS x len(SCENARIOS))."""

import logging

import pytest

from app.session import CallSession
from tests.conftest import FakeWebSocket
from tests.scenarios import structural_checks
from tests.scenarios.definitions import SCENARIOS
from tests.scenarios.harness import run_conversation
from tests.scenarios.judge import judge_scenario

pytestmark = pytest.mark.scenario
logger = logging.getLogger(__name__)

N_TRIALS = 3
PASS_THRESHOLD = 2  # out of N_TRIALS -- LLM output isn't perfectly deterministic


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_scenario(scenario, patched_db_pool):
    hard_failures = []  # true invariants: one violation anywhere is a real regression
    judge_passes = 0
    soft_warnings = []  # best-effort heuristics -- logged, not asserted on

    for trial in range(N_TRIALS):
        session = CallSession(websocket=FakeWebSocket(), account_id=42)
        result = await run_conversation(scenario.consumer_persona, session)

        multi_sentence = structural_checks.one_sentence_per_turn(result.transcript)
        duplicate_calls = structural_checks.tool_called_at_most_once(result.tool_calls)
        if multi_sentence:
            hard_failures.append(f"trial {trial}: multi-sentence turn(s): {multi_sentence}")
        if duplicate_calls:
            hard_failures.append(f"trial {trial}: tool(s) called more than once: {duplicate_calls}")

        if not structural_checks.tool_called_after_confirmation(result.transcript, result.tool_calls):
            soft_warnings.append(f"trial {trial}: no clear confirmation found before a tool call")

        judgment = await judge_scenario(result.transcript, scenario.expected_outcome, result.tool_calls)
        if judgment.outcome_met:
            judge_passes += 1
        else:
            soft_warnings.append(f"trial {trial} judge: {judgment.reasoning}")

    if soft_warnings:
        logger.warning("%s: soft warnings (not asserted on):\n%s", scenario.name, "\n".join(soft_warnings))

    assert not hard_failures, "\n".join(hard_failures)
    assert judge_passes >= PASS_THRESHOLD, (
        f"{scenario.name}: only {judge_passes}/{N_TRIALS} trials met the expected outcome:\n"
        + "\n".join(soft_warnings)
    )
