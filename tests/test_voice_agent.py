"""app/voice_agent.py: barge-in counting, turn-latency sampling, and the
teardown_session wiring (disposition derivation, metrics write, and the
FK-ordering gate on the background compliance audit)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import voice_agent


def _event(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


# --- G2: barge_in_count only increments while Cora is actually mid-speech
# (AgentStartedSpeaking..AgentAudioDone) -- see CallSession.agent_speaking.
# RE-BASELINED (was: every UserStartedSpeaking counted, inflating the
# metric -- e.g. two events firing a second apart for one utterance, or one
# arriving minutes after the agent had already finished talking). ---
async def test_barge_in_counts_only_while_agent_is_speaking(session):
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)  # let the fire-and-forget control-packet task run

    assert session.barge_in_count == 1
    assert any("[Barge-in]" in line for line in session.log_lines)
    assert session.agent_speaking is False


async def test_barge_in_not_counted_when_agent_was_not_speaking(session):
    """A UserStartedSpeaking with no preceding AgentStartedSpeaking (e.g.
    long after the agent finished, or a stray duplicate event) must not
    inflate the count or add a misleading transcript line -- but the audio
    buffer is still cleared unconditionally, since clearing an empty
    buffer is harmless and a real interruption must never be missed."""
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.barge_in_count == 0
    assert not any("[Barge-in]" in line for line in session.log_lines)
    assert any("clear_audio_buffer" in text for text in session.websocket.sent_text)


async def test_barge_in_second_event_after_reset_not_double_counted(session):
    """Two UserStartedSpeaking events for what Deepgram reports as one
    interruption (a real, historically observed case) must count once, not
    twice -- the first resets agent_speaking to False, so a second event
    with no new AgentStartedSpeaking in between is correctly not a second
    barge-in."""
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.barge_in_count == 1


async def test_agent_audio_done_clears_speaking_flag(session):
    """RE-BASELINED: AgentAudioDone no longer clears agent_speaking
    synchronously -- it schedules a delayed clear estimated from bytes
    sent (see the playback-estimate tests below). With zero bytes sent
    (no audio actually queued in this test), the estimate is 0 and the
    flag clears as soon as the scheduled task gets a chance to run --
    hence the sleep(0) to yield control, where the old version needed
    none."""
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    assert session.agent_speaking is True

    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)
    await asyncio.sleep(0)
    assert session.agent_speaking is False


# --- Playback-end estimate: AgentAudioDone fires when Deepgram finishes
# SENDING audio, not when the browser finishes PLAYING it -- confirmed
# live with an 8-second gap between a disclosure's AgentAudioDone and the
# customer actually speaking. agent_speaking must stay True until an
# estimate (bytes sent / the fixed linear16 sample rate) elapses. ---
def test_estimated_playback_remaining_seconds_pure_calculation():
    from datetime import datetime, timedelta

    start = datetime(2026, 1, 1, 12, 0, 0)
    # 48000 bytes/sec (24kHz, mono, 16-bit) -- 24000 bytes is exactly 0.5s.
    remaining = voice_agent._estimated_playback_remaining_seconds(
        bytes_sent=24000, segment_started_at=start, now=start + timedelta(seconds=0.2),
    )
    assert remaining == pytest.approx(0.3, abs=1e-9)


def test_estimated_playback_remaining_seconds_never_negative():
    from datetime import datetime, timedelta

    start = datetime(2026, 1, 1, 12, 0, 0)
    # "now" is already past the estimated playback end.
    remaining = voice_agent._estimated_playback_remaining_seconds(
        bytes_sent=100, segment_started_at=start, now=start + timedelta(seconds=10),
    )
    assert remaining == 0.0


def test_estimated_playback_remaining_seconds_zero_bytes_is_zero():
    from datetime import datetime

    now = datetime(2026, 1, 1, 12, 0, 0)
    assert voice_agent._estimated_playback_remaining_seconds(0, now, now) == 0.0


async def test_agent_speaking_stays_true_until_estimated_playback_elapses(session):
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    # ~0.05s of audio at 48000 bytes/sec.
    voice_agent.on_agent_message(b"\x00" * 2400, session)
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    # Not cleared yet -- the estimated playback hasn't elapsed.
    assert session.agent_speaking is True

    await asyncio.sleep(0.1)
    assert session.agent_speaking is False


async def test_user_started_speaking_during_estimated_playback_counts_as_barge_in(session):
    """The exact live bug: a customer speaking while queued audio is
    still playing -- after AgentAudioDone, but before the estimated
    playback window closes -- must still be counted as a real
    interruption, not silently missed because the flag was already
    cleared."""
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(b"\x00" * 4800, session)  # ~0.1s of audio
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    # Customer interrupts almost immediately -- well before the ~0.1s
    # estimated playback window closes.
    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.barge_in_count == 1
    assert session.agent_speaking is False


async def test_new_utterance_cancels_a_stale_pending_clear(session):
    """A fresh AgentStartedSpeaking before the previous utterance's
    estimated playback finished must not let that stale timer clear
    agent_speaking out from under the new one."""
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(b"\x00" * 4800, session)  # ~0.1s of audio
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    # New utterance starts before the ~0.1s estimate from the first one
    # would have elapsed.
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    assert session.agent_speaking is True

    # Wait past when the FIRST utterance's stale timer would have fired.
    await asyncio.sleep(0.15)
    assert session.agent_speaking is True  # not cleared by the stale timer


async def test_real_barge_in_cancels_pending_clear_task(session):
    """A genuine interruption clears agent_speaking immediately and must
    cancel any pending delayed-clear task -- otherwise a later, harmless-
    but-stale task still exists (cleaned up here for correctness, not
    because leaving it would cause a wrong reading)."""
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(b"\x00" * 48000, session)  # ~1s of audio
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.agent_speaking_clear_task is None


# --- Mini-Miranda interruption: FDCPA requires the disclosure be MADE --
# a barge-in during that specific turn must set a sticky session flag
# (read deterministically by app/audit.py, not left to the LLM judge) and
# re-deliver the disclosure verbatim, the way a real collector would. ---
async def test_mini_miranda_flag_set_then_reset_by_next_assistant_turn(session):
    from app import config

    voice_agent.on_agent_message(
        _event(type="ConversationText", role="assistant", content=config.MINI_MIRANDA_DISCLOSURE), session,
    )
    assert session.last_assistant_turn_was_mini_miranda is True

    voice_agent.on_agent_message(
        _event(type="ConversationText", role="assistant", content="The balance is $1000."), session,
    )
    assert session.last_assistant_turn_was_mini_miranda is False


async def test_mini_miranda_barge_in_sets_interrupted_flag_and_redelivers(session):
    from app import config

    voice_agent.on_agent_message(
        _event(type="ConversationText", role="assistant", content=config.MINI_MIRANDA_DISCLOSURE), session,
    )
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(b"\x00" * 4800, session)  # ~0.1s of audio, still "playing"
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.barge_in_count == 1
    assert session.mini_miranda_interrupted is True
    assert session.agent_connection.sent_inject_agent_messages == [config.MINI_MIRANDA_DISCLOSURE]


async def test_non_mini_miranda_barge_in_does_not_set_interrupted_or_redeliver(session):
    """A barge-in during any OTHER turn is a normal, already-handled
    barge-in -- it must not touch mini_miranda_interrupted or trigger a
    redelivery that was never asked for."""
    voice_agent.on_agent_message(
        _event(type="ConversationText", role="assistant", content="I can offer $800 today."), session,
    )
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(b"\x00" * 4800, session)
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)

    voice_agent.on_agent_message(_event(type="UserStartedSpeaking"), session)
    await asyncio.sleep(0)

    assert session.barge_in_count == 1  # still a real barge-in
    assert session.mini_miranda_interrupted is False
    assert session.agent_connection.sent_inject_agent_messages == []


async def test_mini_miranda_delivered_in_full_does_not_set_interrupted(session):
    """No barge-in at all -- the disclosure played out completely -- must
    never set the flag or redeliver."""
    from app import config

    voice_agent.on_agent_message(
        _event(type="ConversationText", role="assistant", content=config.MINI_MIRANDA_DISCLOSURE), session,
    )
    voice_agent.on_agent_message(_event(type="AgentStartedSpeaking"), session)
    voice_agent.on_agent_message(_event(type="AgentAudioDone"), session)
    await asyncio.sleep(0)

    assert session.mini_miranda_interrupted is False
    assert session.agent_connection.sent_inject_agent_messages == []


async def test_conversation_text_appends_log_and_tracks_user_turn(session):
    voice_agent.on_agent_message(_event(type="ConversationText", role="assistant", content="hi"), session)
    assert session.turn_id == 0  # only user turns bump the turn counter

    voice_agent.on_agent_message(_event(type="ConversationText", role="user", content="yes"), session)
    assert session.turn_id == 1
    assert session.log_lines == [
        session.log_lines[0],  # assistant line (format checked in test_session.py)
        session.log_lines[1],
    ]


async def test_latency_report_dict_records_total_latency_in_ms(session):
    # Deepgram sends LatencyReport as a plain dict, not a typed object (see
    # on_agent_message's docstring) -- several partial reports per turn,
    # only the one carrying total_latency should produce a sample.
    voice_agent.on_agent_message({"type": "LatencyReport", "ttt_token_latency": 0.2}, session)
    voice_agent.on_agent_message({"type": "LatencyReport", "ttt_text_latency": 0.21}, session)
    voice_agent.on_agent_message({"type": "LatencyReport", "tts_latency": 0.05}, session)
    voice_agent.on_agent_message({"type": "LatencyReport", "total_latency": 0.812}, session)

    assert session.latency_samples_ms == [812.0]


async def test_latency_report_missing_total_latency_records_nothing(session):
    voice_agent.on_agent_message({"type": "LatencyReport", "ttt_token_latency": 0.2}, session)
    assert session.latency_samples_ms == []


async def test_non_latency_dict_message_is_ignored(session):
    voice_agent.on_agent_message({"type": "SomethingElse"}, session)
    assert session.latency_samples_ms == []


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
