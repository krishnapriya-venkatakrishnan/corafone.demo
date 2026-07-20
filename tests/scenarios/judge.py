"""Scenario-aware judge: structurally like app/audit.py's compliance judge,
but scored against a scenario's specific expected outcome rather than the
general compliance dimensions EvaluationReport covers -- those are different
questions ("was Mini-Miranda given?" vs. "did THIS scenario's edge case get
handled correctly?")."""

from pydantic import BaseModel, Field

from app import config
from tests.scenarios.harness import openai_client


class ScenarioJudgment(BaseModel):
    outcome_met: bool = Field(description="True if the transcript matches the expected outcome described.")
    reasoning: str = Field(description="Concise justification, quoting the relevant transcript line(s).")


async def judge_scenario(
    transcript: list[str], expected_outcome: str, tool_calls: list[str] | None = None
) -> ScenarioJudgment:
    """`tool_calls` is the harness's own authoritative record of which tools
    actually fired (app/tools.py's real, idempotency-guarded handlers) --
    passed alongside the transcript so the judge doesn't have to (unreliably)
    infer "was the tool called" purely from how Cora's confirmation happens
    to be worded."""
    transcript_text = "\n".join(transcript)
    tool_calls_text = ", ".join(tool_calls) if tool_calls else "none"
    response = await openai_client.beta.chat.completions.parse(
        model=config.OPENAI_JUDGE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are grading a single test scenario for an automated debt-collection "
                    "voice agent. You'll be given a transcript, the exact list of backend tools "
                    "that were actually called during the call, and a description of the behavior "
                    "expected in this specific scenario -- judge only against that description. "
                    "The transcript includes `[tool called: X]` lines placed exactly where each "
                    "tool fired chronologically relative to the dialogue -- use those, not wording "
                    "or tone, to judge whether/when/how many times a tool was actually called. Each "
                    "is followed by `[tool args: ...]` and `[tool result: ...]` lines showing "
                    "exactly what was sent and what the tool actually authorized -- use these as "
                    "ground truth for whether a figure, date, or refusal Cora later spoke was "
                    "faithful to the tool, invented, or dropped a detail (e.g. a floor-minimum "
                    "sentence)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Expected outcome:\n{expected_outcome}\n\n"
                    f"Tool calls actually made during this call (in order): {tool_calls_text}\n\n"
                    f"Transcript:\n{transcript_text}"
                ),
            },
        ],
        response_format=ScenarioJudgment,
    )
    return response.choices[0].message.parsed
