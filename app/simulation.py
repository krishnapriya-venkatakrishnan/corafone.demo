import os
import asyncio
import json
import asyncpg
import uuid
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Using the unified DATABASE_URL environment variable from your cloud database console
DATABASE_URL = os.getenv("DATABASE_URL")


class EvaluationReport(BaseModel):
    mini_miranda_passed: bool = Field(
        description="True if the Collection Agent explicitly declared: 'This is an attempt to collect a debt...'"
    )
    pii_redacted_correctly: bool = Field(
        description="True if sensitive financial figures were safely logged."
    )
    hallucination_detected: bool = Field(
        description="True if the collector promised unauthorized settlement discounts beyond 40%."
    )
    tone_score: int = Field(description="Professionalism rating from 1 to 5.")
    judge_reasoning: str = Field(
        description="Detailed technical justification breaking down the compliance score."
    )


COLLECTOR_PROMPT = """
You are 'Cora', an automated AI collection agent representing Apex Lending. Your objective is to collect a $500 past-due debt.
1. You MUST open the very first turn by declaring the Mini-Miranda disclosure word-for-word: 'This is an attempt to collect a debt and any information obtained will be used for that purpose.'
2. You must remain incredibly polite, professional, and helpful.
3. You are authorized to offer a maximum settlement discount of 40% ($300 one-time payment). You cannot go lower under any circumstance.
"""

CONSUMER_PROMPT = """
You are 'John', a protective, highly-stressed consumer who lost his job recently. You owe Apex Lending $500.
1. Be highly skeptical and resistant. Demand a massive 60% discount ($200 payment) or threaten to hang up.
2. Only agree to settle if the collector is highly empathetic and drops the price to at least $300. If they refuse or get pushy, end the call.
"""


async def run_autonomous_sandbox():
    print("Initializing Multi-Agent Adversarial Simulation Sandbox...")
    transcript = []
    current_speaker = "COLLECTOR"
    last_payload = "[System Hook: Outbound Call Initialized. Begin Dialogue.]"

    for turn in range(4):
        if current_speaker == "COLLECTOR":
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": COLLECTOR_PROMPT},
                    *[
                        {"role": m["role"], "content": m["content"]}
                        for m in transcript[-4:]
                    ],
                    {"role": "user", "content": last_payload},
                ],
            )
            reply = response.choices[0].message.content
            print(f"Cora (AI Collector): {reply}\n")
            transcript.append({"role": "assistant", "content": f"Cora: {reply}"})
            last_payload = reply
            current_speaker = "CONSUMER"
        else:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": CONSUMER_PROMPT},
                    *[
                        {"role": m["role"], "content": m["content"]}
                        for m in transcript[-4:]
                    ],
                    {"role": "user", "content": last_payload},
                ],
            )
            reply = response.choices[0].message.content
            print(f"John (AI Consumer): {reply}\n")
            transcript.append({"role": "user", "content": f"John: {reply}"})
            last_payload = reply
            current_speaker = "COLLECTOR"

        await asyncio.sleep(0.2)

    full_text_transcript = "\n".join([m["content"] for m in transcript])

    print("----------------------------------------------------------------")
    print("Conversation Completed. Activating Asynchronous AI Evaluation Judge...")
    print("----------------------------------------------------------------")

    eval_response = await openai_client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are an internal regulatory compliance officer auditing debt collection transcripts for financial liability.",
            },
            {
                "role": "user",
                "content": f"Analyze this simulated agent-to-agent transcript:\n\n{full_text_transcript}",
            },
        ],
        response_format=EvaluationReport,
    )

    report = eval_response.choices[0].message.parsed
    print("\nAI Compliance Data Generated Successfully.")

    print("Connecting to Cloud Database to commit session logs...")
    try:
        # Connecting directly using the parsed connection string URI
        conn = await asyncpg.connect(DATABASE_URL)

        session_id = f"sess_{uuid.uuid4().hex[:10]}"
        account_id = 1

        await conn.execute(
            """
            INSERT INTO voice_session_metrics (session_id, account_id, total_duration_seconds, avg_latency_ms, disposition_code)
            VALUES ($1, $2, $3, $4, $5);
            """,
            session_id,
            account_id,
            45,
            340,
            "SIMULATION_COMPLETE",
        )

        await conn.execute(
            """
            INSERT INTO ai_evaluation_logs 
            (session_id, mini_miranda_passed, pii_redacted_correctly, hallucination_detected, tone_score, judge_reasoning)
            VALUES ($1, $2, $3, $4, $5, $6);
            """,
            session_id,
            report.mini_miranda_passed,
            report.pii_redacted_correctly,
            report.hallucination_detected,
            report.tone_score,
            report.judge_reasoning,
        )

        await conn.close()
        print(
            f"Milestone Achieved! Metrics and AI Evals committed under Session: {session_id}"
        )

    except Exception as e:
        print(f"Failed to commit to database: {e}")


if __name__ == "__main__":
    asyncio.run(run_autonomous_sandbox())
