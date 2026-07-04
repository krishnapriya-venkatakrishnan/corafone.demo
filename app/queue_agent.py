"""Agentic call-queue decision: given a deterministically pre-filtered list
of candidate accounts (see app/dashboard_api.py's /queue/next), decides which
ONE to call next -- or none. Structurally like app/audit.py's compliance
judge (same structured-output pattern), but the judgment here is about
whether an account should be called at all, using signals a plain status
filter can't safely capture -- e.g. a past call's `right_to_cease_honored`
recorded a stop-contact request even though `accounts.status` still says
ACTIVE."""

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
        "mattered (e.g. a prior stop-contact request, a pending callback, prohibited conduct on a past call)."
    )


async def decide_next_call(candidates: list[dict]) -> QueueDecision:
    """`candidates` are accounts that already passed a deterministic status
    filter (not SETTLED/DO_NOT_CALL) -- this call's job is the judgment a
    status column can't make, not re-deciding eligibility from scratch."""
    response = await openai_client.beta.chat.completions.parse(
        model=config.OPENAI_JUDGE_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are deciding which debt-collection account to call next out of a list of "
                    "candidates that have already passed a basic eligibility filter. Pick exactly one "
                    "account_id to call next, or null if none should be called right now. "
                    "Exclude (or deprioritize) an account if its most recent call shows "
                    "`right_to_cease_honored` is not null -- that means the customer asked to stop "
                    "being contacted in a past call, regardless of what the account's status field "
                    "says now. Also weigh `prohibited_conduct_detected` on a past call (may need human "
                    "review before calling again) and any pending scheduled callback (calling before "
                    "that time contradicts what was already promised). Prefer accounts with no recent "
                    "call history or the longest time since their last call when nothing else "
                    "distinguishes the candidates."
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
