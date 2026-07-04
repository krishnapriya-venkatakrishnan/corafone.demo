"""Agentic call-queue decision: given a deterministically pre-filtered list
of candidate accounts (see app/dashboard_api.py's /queue/next -- status,
requires_manual_review, and pending-callback/payment-due dates are all
already enforced before candidates get here), decides which ONE to call next
-- or none. Structurally like app/audit.py's compliance judge (same
structured-output pattern), but the judgment here is the kind of prioritization
a plain filter can't make: which of the *already-eligible* accounts is most
worth calling right now."""

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from . import config

openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)


class QueueDecision(BaseModel):
    account_id: int | None = Field(
        description="The account_id to call next, or null if none of the candidates should be called right now."
    )
    reasoning: str = Field(
        description="Why this account was chosen, or why none were -- cite the specific signal(s) that "
        "mattered (e.g. prohibited conduct on a past call, or no history vs. recently contacted)."
    )


async def decide_next_call(candidates: list[dict]) -> QueueDecision:
    """`candidates` have already passed every deterministic eligibility check
    (status, requires_manual_review, pending-callback/payment-due dates) --
    this call's job is choosing the best of what's left, not re-deciding
    eligibility from scratch."""
    response = await openai_client.beta.chat.completions.parse(
        model=config.OPENAI_JUDGE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are deciding which debt-collection account to call next out of a list of "
                    "candidates that have already passed every eligibility check (status, do-not-call "
                    "flags, and any pending callback or payment due date). Pick exactly one account_id "
                    "to call next, or null if none should be called right now. As a safety net, still "
                    "exclude an account if its most recent call shows `right_to_cease_honored` is not "
                    "null (a stop-contact request should have already been flagged for manual review, "
                    "but don't call anyway if you see one). Also weigh `prohibited_conduct_detected` on "
                    "a past call (may warrant human review before calling again). Prefer accounts with "
                    "no recent call history, or the longest time since their last call, when nothing "
                    "else distinguishes the candidates."
                ),
            },
            {
                "role": "user",
                "content": f"Candidates:\n{candidates}",
            },
        ],
        response_format=QueueDecision,
    )
    return response.choices[0].message.parsed
