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


async def judge_scenario(transcript: list[str], expected_outcome: str) -> ScenarioJudgment:
    transcript_text = "\n".join(transcript)
    response = await openai_client.beta.chat.completions.parse(
        model=config.OPENAI_JUDGE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are grading a single test scenario for an automated debt-collection "
                    "voice agent. You'll be given a transcript and a description of the behavior "
                    "expected in this specific scenario -- judge only against that description."
                ),
            },
            {
                "role": "user",
                "content": f"Expected outcome:\n{expected_outcome}\n\nTranscript:\n{transcript_text}",
            },
        ],
        response_format=ScenarioJudgment,
    )
    return response.choices[0].message.parsed
