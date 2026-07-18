"""app/session.py: CallSession defaults and the append_call_log formatting
helper used by both app/tools.py and app/voice_agent.py."""

import re

from app.session import CallSession, append_call_log


def test_call_session_defaults(fake_websocket):
    s = CallSession(websocket=fake_websocket)

    assert re.fullmatch(r"sess_[0-9a-f]{10}", s.session_id)
    assert s.account_id is None
    assert s.settlement_settled is False
    assert s.payment_plan_created is False
    assert s.barge_in_count == 0
    assert s.error_count == 0
    assert s.log_lines == []
    assert s.latency_samples_ms == []


def test_each_session_gets_a_unique_session_id(fake_websocket):
    a = CallSession(websocket=fake_websocket)
    b = CallSession(websocket=fake_websocket)

    assert a.session_id != b.session_id


def test_append_call_log_formats_and_appends(session):
    append_call_log(session, "assistant", "Hello, this is Cora.")

    assert len(session.log_lines) == 1
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[assistant\] Hello, this is Cora\.", session.log_lines[0])


def test_append_call_log_preserves_order(session):
    append_call_log(session, "assistant", "first")
    append_call_log(session, "user", "second")

    assert session.log_lines[0].endswith("[assistant] first")
    assert session.log_lines[1].endswith("[user] second")
