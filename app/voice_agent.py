"""Deepgram Voice Agent protocol relay: opens the managed STT+LLM+TTS
session, relays audio and control packets to/from the browser, and
dispatches function calls to app/tools.py. Deepgram owns turn-taking and
barge-in; this module does not. Also curates a per-call transcript and
uploads it to Supabase Storage on teardown -- see app/storage.py."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.types import ThinkSettingsV1, SpeakSettingsV1
from deepgram.agent.v1.types import (
    AgentV1Settings,
    AgentV1SettingsAgentContext,
    AgentV1SettingsAgentContextListen,
    AgentV1SettingsAudio,
    AgentV1SettingsAudioInput,
    AgentV1SettingsAudioOutput,
)

from . import audit, config, db, storage
from .session import CallSession, append_call_log
from .tools import handle_function_call_request

logger = logging.getLogger("corafone")

deepgram_client = AsyncDeepgramClient(api_key=config.DEEPGRAM_API_KEY)


async def send_control_packet(session: CallSession, packet_type: str) -> None:
    """Sends a small JSON control frame to the browser (see app.js's
    ws.onmessage handler for the matching clear-buffer behavior)."""
    try:
        await session.websocket.send_text(json.dumps({"type": packet_type}))
    except Exception:
        logger.exception("Failed to send '%s' control packet to client.", packet_type)


def on_agent_message(message: Any, session: CallSession) -> None:
    """Deepgram socket event callback. Raw `bytes` are synthesized speech,
    relayed straight to the browser. `LatencyReport` arrives as a plain
    dict, not a typed object -- deepgram-sdk 7.4.0's typed models don't
    include it, so the SDK's own construct_type() falls back to an untyped
    dict for this one message, which is why it needs its own branch before
    the generic attribute-based dispatch below (a dict has no `.type`
    attribute; `getattr(message, "type", None)` would silently read as
    None for it). Everything else is a JSON event discriminated by `.type`.
    """
    if isinstance(message, bytes):
        asyncio.create_task(session.websocket.send_bytes(message))
        return

    if isinstance(message, dict):
        # Deepgram reports latency as several partial LatencyReport
        # messages per turn (ttt_token_latency, ttt_text_latency,
        # tts_latency, ttt_tool_latency); `total_latency` is the one that
        # matters here -- Deepgram's own measured gap from the customer's
        # utterance to Cora's reply, in seconds.
        if message.get("type") == "LatencyReport" and message.get("total_latency") is not None:
            session.latency_samples_ms.append(message["total_latency"] * 1000)
        return

    message_type = getattr(message, "type", None)

    if message_type == "UserStartedSpeaking":
        # Flux already confirmed this is real speech -- stop playback now.
        logger.info("[Barge-in] Customer started speaking -- clearing playback.")
        append_call_log(session, "Barge-in", "Customer started speaking -- clearing playback.")
        session.barge_in_count += 1
        asyncio.create_task(send_control_packet(session, "clear_audio_buffer"))
        return

    if message_type == "FunctionCallRequest":
        asyncio.create_task(handle_function_call_request(message, session))
        return

    if message_type == "ConversationText":
        logger.info("[%s]: %s", message.role, message.content)
        append_call_log(session, message.role, message.content)
        if message.role == "user":
            # A new customer utterance is a new conversational turn -- see
            # NegotiationState's docstring and app/tools.py's validation
            # cache, which this invalidates.
            session.turn_id += 1
        return

    if message_type in ("Error", "Warning"):
        description = getattr(message, "description", message)
        log_fn = logger.error if message_type == "Error" else logger.warning
        log_fn("Deepgram Voice Agent %s: %s", message_type, description)
        append_call_log(session, message_type, str(description))
        return

    # Welcome, SettingsApplied, AgentThinking, AgentStartedSpeaking,
    # AgentAudioDone, etc. -- console-only, not curated into the transcript.
    logger.info("Voice Agent event: %s", message_type)


def on_agent_error(error: Any, **kwargs) -> None:
    logger.error("Deepgram Voice Agent stream error: %s", error)


async def initialize_agent_connection(session: CallSession) -> None:
    """Opens the Voice Agent WebSocket and sends the Settings message
    (STT/LLM/TTS + tools + greeting). Runs immediately on connect --
    Deepgram speaks the greeting as soon as settings are applied, no mic
    audio needed first."""
    logger.info("Opening Deepgram Voice Agent session...")
    session.agent_context = deepgram_client.agent.v1.connect()
    session.agent_connection = await session.agent_context.__aenter__()
    session.agent_connection.on(
        EventType.MESSAGE, lambda message, **kwargs: on_agent_message(message, session)
    )
    session.agent_connection.on(EventType.ERROR, on_agent_error)
    session.agent_listen_task = asyncio.create_task(session.agent_connection.start_listening())

    await session.agent_connection.send_settings(
        AgentV1Settings(
            audio=AgentV1SettingsAudio(
                input=AgentV1SettingsAudioInput(
                    encoding=config.AUDIO_ENCODING, sample_rate=config.AUDIO_SAMPLE_RATE
                ),
                output=AgentV1SettingsAudioOutput(
                    encoding=config.AUDIO_ENCODING,
                    sample_rate=config.AUDIO_SAMPLE_RATE,
                    container="none",
                ),
            ),
            agent=AgentV1SettingsAgentContext(
                listen=AgentV1SettingsAgentContextListen(
                    provider={
                        "type": "deepgram",
                        "version": "v2",
                        "model": config.DEEPGRAM_AGENT_STT_MODEL,
                    }
                ),
                think=ThinkSettingsV1(
                    provider={"type": "open_ai", "model": config.OPENAI_MODEL},
                    prompt=config.build_system_prompt(session.customer_name, session.account_balance),
                    functions=[
                        config.VALIDATE_CONSUMER_PROPOSAL_FUNCTION_SCHEMA,
                        config.RECORD_AGREEMENT_FUNCTION_SCHEMA,
                    ],
                ),
                speak=SpeakSettingsV1(
                    provider={"type": "deepgram", "model": config.DEEPGRAM_TTS_MODEL}
                ),
                # Identity check only -- see SYSTEM_PROMPT rule 1.
                greeting=config.build_greeting(session.customer_name),
            ),
        )
    )
    logger.info("Voice Agent settings sent -- awaiting SettingsApplied + greeting.")


async def teardown_session(session: CallSession) -> None:
    """Releases Deepgram resources when the call ends, however it ends, and
    uploads the curated transcript to Supabase Storage."""
    if session.agent_listen_task is not None:
        session.agent_listen_task.cancel()
    if session.agent_context is not None:
        logger.info("Shutting down active Deepgram Voice Agent session.")
        await session.agent_context.__aexit__(None, None, None)

    transcript_path = None
    if session.account_id is not None and session.log_lines:
        path = f"{session.account_id}/{session.call_started_at:%Y%m%dT%H%M%SZ}/log.txt"
        try:
            await storage.upload_call_log(path, "\n".join(session.log_lines))
            logger.info("Uploaded call transcript to Supabase Storage: %s", path)
            transcript_path = path
        except Exception:
            logger.exception("Failed to upload call transcript to Supabase Storage.")
            session.error_count += 1

    if session.account_id is not None:
        disposition_code = session.agreement_disposition or "NO_ACTION"

        if disposition_code == "ESCALATED_NO_AGREEMENT":
            # Deterministic flag, set here rather than left to the LLM judge
            # to infer -- see app/negotiation.py's candidate exhaustion
            # (selection returning None, no legal arrangement reachable).
            try:
                await db.set_requires_manual_review(session.account_id)
            except Exception:
                logger.exception(
                    "Failed to flag account %s for manual review (session %s).",
                    session.account_id, session.session_id,
                )

        total_duration_seconds = int((datetime.now() - session.call_started_at).total_seconds())
        avg_latency_ms = (
            int(sum(session.latency_samples_ms) / len(session.latency_samples_ms))
            if session.latency_samples_ms
            else 0
        )

        try:
            await db.create_voice_session_metrics(
                session.session_id,
                session.account_id,
                total_duration_seconds,
                avg_latency_ms,
                session.barge_in_count,
                disposition_code,
                session.error_count,
                transcript_path,
            )
        except Exception:
            logger.exception("Failed to write voice_session_metrics for session %s.", session.session_id)
        else:
            # FK-dependent on the row above, and an LLM judge call shouldn't
            # add latency to teardown -- runs in the background.
            if session.log_lines:
                asyncio.create_task(audit.run_compliance_audit(session))
