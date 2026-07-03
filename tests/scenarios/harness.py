"""Drives a full text conversation between Cora and a scripted adversarial
consumer persona, using the *real* system prompt, tool schemas, and tool
execution functions -- entirely over OpenAI's chat completions API, no
Deepgram/audio involved (this tests decision logic, not voice transport).
The DB must already be mocked by the caller (see tests/conftest.py's
patched_db_pool fixture) -- nothing here talks to real Supabase.
"""

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app import config, tools
from app.session import CallSession

openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

_END_PHRASES = ("bye", "goodbye", "have a good day", "talk soon")
_MAX_TOOL_CALLS_PER_TURN = 5  # safety cap, not a realistic expectation


def _to_openai_tool(flat_schema: dict) -> dict:
    """config.py's tool schemas are Deepgram's flat shape ({name, description,
    parameters}) -- OpenAI's native chat.completions API wants them nested
    under {"function": {...}}."""
    return {
        "type": "function",
        "function": {
            "name": flat_schema["name"],
            "description": flat_schema["description"],
            "parameters": flat_schema["parameters"],
        },
    }


_TOOLS = [
    _to_openai_tool(config.SETTLEMENT_FUNCTION_SCHEMA),
    _to_openai_tool(config.SCHEDULE_CALLBACK_FUNCTION_SCHEMA),
    _to_openai_tool(config.OFFER_PAYMENT_PLAN_FUNCTION_SCHEMA),
]


@dataclass
class ConversationResult:
    transcript: list[str] = field(default_factory=list)  # "role: content" lines
    tool_calls: list[str] = field(default_factory=list)  # tool names called, in order


async def _collector_turn(
    messages: list[dict], session: CallSession, result: ConversationResult
) -> str | None:
    """One Cora turn. May involve a tool call first -- dispatched through the
    real app/tools.py `_FUNCTION_CALL_HANDLERS` (the same idempotency-guarded
    functions the live system uses), not Deepgram's FunctionCallRequest
    envelope, which doesn't exist outside a real Voice Agent session."""
    for _ in range(_MAX_TOOL_CALLS_PER_TURN):
        response = await openai_client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            tools=_TOOLS,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content})
            return message.content

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            }
        )
        for tool_call in message.tool_calls:
            handler = tools._FUNCTION_CALL_HANDLERS.get(tool_call.function.name)
            args = json.loads(tool_call.function.arguments)
            if handler is None:
                tool_result = {"status": "error", "message": "unknown tool"}
            else:
                try:
                    tool_result = await handler(args, session)
                except Exception as exc:
                    tool_result = {"status": "error", "message": str(exc)}
            result.tool_calls.append(tool_call.function.name)
            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(tool_result)}
            )

    return None  # exhausted the per-turn tool-call budget without a spoken reply


async def _consumer_turn(consumer_persona: str, consumer_view: list[dict]) -> str:
    """One turn for the scripted adversarial customer persona."""
    response = await openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": consumer_persona}, *consumer_view],
    )
    return response.choices[0].message.content


async def run_conversation(
    consumer_persona: str, session: CallSession, max_turns: int = 10
) -> ConversationResult:
    """Seeds the conversation with the real fixed greeting (no LLM round-trip
    for that, matching the live system), then alternates customer/Cora turns
    until a natural end or `max_turns` is reached."""
    result = ConversationResult()
    greeting = config.GREETING_IDENTITY_CHECK
    result.transcript.append(f"assistant: {greeting}")

    collector_messages = [
        {"role": "system", "content": config.build_system_prompt()},
        {"role": "assistant", "content": greeting},
    ]
    consumer_view = [{"role": "assistant", "content": greeting}]

    for _ in range(max_turns):
        customer_reply = await _consumer_turn(consumer_persona, consumer_view)
        result.transcript.append(f"user: {customer_reply}")
        consumer_view.append({"role": "user", "content": customer_reply})
        collector_messages.append({"role": "user", "content": customer_reply})

        if any(phrase in customer_reply.lower() for phrase in _END_PHRASES):
            break

        cora_reply = await _collector_turn(collector_messages, session, result)
        if cora_reply is None:
            break
        result.transcript.append(f"assistant: {cora_reply}")
        consumer_view.append({"role": "assistant", "content": cora_reply})

    return result
