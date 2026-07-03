"""app/db.py: verifies each function issues the right SQL against the right
parameters, using the mocked pool/connection from conftest.py -- no real
Postgres involved."""

from datetime import date, datetime

import pytest

from app import db


async def test_get_account_id_by_phone_returns_id(patched_db_pool):
    patched_db_pool.fetchrow.return_value = {"account_id": 42}

    account_id = await db.get_account_id_by_phone("+15550199")

    assert account_id == 42
    patched_db_pool.fetchrow.assert_awaited_once_with(
        "SELECT account_id FROM accounts WHERE phone_number = $1", "+15550199"
    )


async def test_get_account_id_by_phone_raises_when_not_found(patched_db_pool):
    patched_db_pool.fetchrow.return_value = None

    with pytest.raises(ValueError):
        await db.get_account_id_by_phone("+10000000")


async def test_apply_settlement(patched_db_pool, mock_db_conn):
    await db.apply_settlement(42)

    mock_db_conn.execute.assert_awaited_once()
    query, account_id = mock_db_conn.execute.call_args.args
    assert "UPDATE accounts" in query and "SETTLED" in query
    assert account_id == 42


async def test_create_payment_plan_writes_plan_and_updates_status(patched_db_pool, mock_db_conn):
    await db.create_payment_plan(42, 5, 100.0, 500.0, date(2026, 7, 10))

    assert mock_db_conn.execute.await_count == 2
    plan_call, status_call = mock_db_conn.execute.call_args_list
    assert "INSERT INTO payment_plans" in plan_call.args[0]
    assert plan_call.args[1:] == (42, 5, 100.0, 500.0, date(2026, 7, 10))
    assert "PAYMENT_PLAN_ACTIVE" in status_call.args[0]
    assert status_call.args[1] == 42


async def test_create_scheduled_callback(patched_db_pool, mock_db_conn):
    when = datetime(2026, 7, 4, 18, 0)
    await db.create_scheduled_callback(42, when)

    mock_db_conn.execute.assert_awaited_once()
    query, account_id, callback_time = mock_db_conn.execute.call_args.args
    assert "INSERT INTO scheduled_callbacks" in query
    assert account_id == 42
    assert callback_time == when


async def test_log_communication(patched_db_pool, mock_db_conn):
    await db.log_communication(42, "Settlement processed.")

    mock_db_conn.execute.assert_awaited_once()
    query, account_id, content = mock_db_conn.execute.call_args.args
    assert "INSERT INTO communication_logs" in query
    assert account_id == 42
    assert content == "Settlement processed."


async def test_create_voice_session_metrics(patched_db_pool, mock_db_conn):
    await db.create_voice_session_metrics("sess_1", 42, 90, 850, 2, "SETTLED", 1)

    mock_db_conn.execute.assert_awaited_once()
    args = mock_db_conn.execute.call_args.args
    assert "INSERT INTO voice_session_metrics" in args[0]
    assert args[1:] == ("sess_1", 42, 90, 850, 2, "SETTLED", 1)


async def test_create_ai_evaluation_log(patched_db_pool, mock_db_conn):
    await db.create_ai_evaluation_log(
        "sess_1", True, True, False, True, False, None, 5, "Solid call.", 0.0045
    )

    mock_db_conn.execute.assert_awaited_once()
    args = mock_db_conn.execute.call_args.args
    assert "INSERT INTO ai_evaluation_logs" in args[0]
    assert args[1:] == ("sess_1", True, True, False, True, False, None, 5, "Solid call.", 0.0045)
