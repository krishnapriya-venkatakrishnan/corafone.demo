"""Shared fixtures. No real network/DB/LLM calls anywhere in this suite --
everything below the boundary (asyncpg, OpenAI, the WebSocket) is mocked."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.session import CallSession


class FakeWebSocket:
    """Records every JSON/text frame sent, instead of hitting a real socket."""

    def __init__(self):
        self.sent_text: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)

    def sent_packets(self) -> list[dict]:
        return [json.loads(t) for t in self.sent_text]


class FakeAgentConnection:
    """Stands in for Deepgram's agent connection object."""

    def __init__(self):
        self.sent_function_call_responses: list[SimpleNamespace] = []
        self.sent_inject_messages: list[str] = []

    async def send_function_call_response(self, response) -> None:
        self.sent_function_call_responses.append(response)

    async def send_inject_user_message(self, message) -> None:
        self.sent_inject_messages.append(message.content)


@pytest.fixture
def fake_websocket() -> FakeWebSocket:
    return FakeWebSocket()


@pytest.fixture
def session(fake_websocket) -> CallSession:
    """A CallSession wired to a fake websocket/agent connection, ready to
    have tool calls or voice_agent events dispatched at it."""
    s = CallSession(websocket=fake_websocket, account_id=42)
    s.agent_connection = FakeAgentConnection()
    return s


def make_function_call(name: str, args: dict, call_id: str = "call_1") -> SimpleNamespace:
    """Builds a fake Deepgram FunctionCallRequest message."""
    return SimpleNamespace(
        functions=[SimpleNamespace(name=name, arguments=json.dumps(args), id=call_id)]
    )


@pytest.fixture
def mock_db_conn():
    """A fake asyncpg connection: .execute/.fetchrow are AsyncMocks, and
    .transaction() is an async context manager, matching how app/db.py uses
    the real asyncpg API."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=None)
    transaction_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction_cm)

    return conn


@pytest.fixture
def mock_pool(mock_db_conn):
    """A fake asyncpg.Pool whose .acquire() yields mock_db_conn."""
    pool = MagicMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=mock_db_conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    # get_account_id_by_phone calls _pool.fetchrow directly, not via acquire()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def patched_db_pool(monkeypatch, mock_pool):
    """Points app.db's module-level pool at our fake pool for the test."""
    from app import db

    monkeypatch.setattr(db, "_pool", mock_pool)
    return mock_pool
