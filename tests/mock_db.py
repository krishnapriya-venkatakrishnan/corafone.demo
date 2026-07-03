"""Shared mock-Postgres construction, usable both as pytest fixtures
(tests/conftest.py) and as a plain context manager outside pytest --
app/dashboard_api.py's scenario-runner endpoint uses this directly so
clicking "Run Scenarios" in the dashboard never touches the real Supabase
account, exactly like the pytest suite doesn't."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock


def build_mock_db_conn() -> MagicMock:
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


def build_mock_pool(conn: MagicMock | None = None) -> MagicMock:
    """A fake asyncpg.Pool whose .acquire() yields `conn` (or a fresh one)."""
    conn = conn if conn is not None else build_mock_db_conn()

    pool = MagicMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    # Several dashboard read functions (app/db.py) call _pool.fetchrow/.fetch
    # directly, not via acquire().
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    return pool


@contextmanager
def mocked_db():
    """Swaps app.db's module-level pool for a mock one for the duration of
    the block, restoring the original pool afterward."""
    from app import db

    original_pool = db._pool
    db._pool = build_mock_pool()
    try:
        yield db._pool
    finally:
        db._pool = original_pool
