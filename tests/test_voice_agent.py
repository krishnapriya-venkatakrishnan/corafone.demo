"""app/voice_agent.py: barge-in counting, turn-latency sampling, and the
teardown_session wiring (disposition derivation, metrics write, and the
FK-ordering gate on the background compliance audit)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import voice_agent


def _event(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


async def test_barge_in_increments_counter_and_appends_log(session):
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)  # let the fire-and-forget control-packet task run

    assert session.barge_in_count == 2
    assert any("[Barge-in]" in line for line in session.log_lines)


async def test_conversation_text_appends_log_and_tracks_user_turn(session):
    voice_agent.on_agent_message(_event(type="ConversationText", role="assistant", content="hi"), session)
    assert session.last_user_turn_at is None  # only user turns start the latency clock
    assert session.turn_id == 0  # only user turns bump the turn counter

    voice_agent.on_agent_message(_event(type="ConversationText", role="user", content="yes"), session)
    assert session.last_user_turn_at is not None
    assert session.turn_id == 1
    assert session.log_lines == [
        session.log_lines[0],  # assistant line (format checked in test_session.py)
        session.log_lines[1],
    ]


async def test_agent_started_speaking_records_latency_sample_once(session):
    voice_agent.on_agent_message(_event(type="ConversationText", role="user", content="yes"), session)
    await asyncio.sleep(0.03)
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)

    assert len(session.latency_samples_ms) == 1
    assert session.latency_samples_ms[0] >= 20
    assert session.last_user_turn_at is None

    # A second AgentStartedSpeaking with no intervening user turn adds nothing.
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    assert len(session.latency_samples_ms) == 1


async def test_routine_events_are_not_curated_into_the_transcript(session):
    for event_type in ("Welcome", "SettingsApplied", "AgentThinking", "AgentAudioDone", "History"):
        voice_agent.on_agent_message(_event(type=event_type), session)

    assert session.log_lines == []


async def test_teardown_derives_disposition_and_writes_metrics(session):
    session.log_lines = ["2026-01-01 00:00:00 [assistant] hi"]
    session.agreement_disposition = "SETTLED"
    session.agent_listen_task = None
    session.agent_context = None

    with patch("app.voice_agent.storage.upload_call_log", new=AsyncMock()), \
         patch("app.voice_agent.db.create_voice_session_metrics", new=AsyncMock()) as create_metrics, \
         patch("app.voice_agent.audit.run_compliance_audit", new=AsyncMock()) as run_audit:
        await voice_agent.teardown_session(session)
        await asyncio.sleep(0)

    create_metrics.assert_awaited_once()
    args = create_metrics.call_args.args
    assert args[0] == session.session_id
    assert args[5] == "SETTLED"
    run_audit.assert_awaited_once_with(session)


async def test_teardown_disposition_precedence_no_action(session):
    session.log_lines = []
    session.agent_listen_task = None
    session.agent_context = None

    with patch("app.voice_agent.db.create_voice_session_metrics", new=AsyncMock()) as create_metrics, \
         patch("app.voice_agent.audit.run_compliance_audit", new=AsyncMock()) as run_audit:
        await voice_agent.teardown_session(session)

    assert create_metrics.call_args.args[5] == "NO_ACTION"
    run_audit.assert_not_called()  # no transcript to judge


async def test_teardown_skips_audit_when_metrics_write_fails(session):
    session.log_lines = ["line"]
    session.agent_listen_task = None
    session.agent_context = None

    with patch("app.voice_agent.storage.upload_call_log", new=AsyncMock()), \
         patch(
             "app.voice_agent.db.create_voice_session_metrics",
             new=AsyncMock(side_effect=RuntimeError("db down")),
         ), patch("app.voice_agent.audit.run_compliance_audit", new=AsyncMock()) as run_audit:
        await voice_agent.teardown_session(session)
        await asyncio.sleep(0)

    run_audit.assert_not_called()


async def test_teardown_counts_storage_upload_failure_as_an_error(session):
    session.log_lines = ["line"]
    session.agent_listen_task = None
    session.agent_context = None

    with patch(
        "app.voice_agent.storage.upload_call_log", new=AsyncMock(side_effect=RuntimeError("storage down"))
    ), patch("app.voice_agent.db.create_voice_session_metrics", new=AsyncMock()) as create_metrics, \
         patch("app.voice_agent.audit.run_compliance_audit", new=AsyncMock()):
        await voice_agent.teardown_session(session)

    assert session.error_count == 1
    assert create_metrics.call_args.args[6] == 1  # error_count reached the metrics write
