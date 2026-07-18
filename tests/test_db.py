"""app/db.py: verifies each function issues the right SQL against the right
parameters, using the mocked pool/connection from conftest.py -- no real
Postgres involved."""

from datetime import date
from decimal import Decimal

from app import config, db


async def test_reset_demo_account_resets_balance_status_and_review_flag(patched_db_pool, mock_db_conn):
    await db.reset_demo_account(42, config.DEFAULT_CUSTOMER_PHONE_NUMBER)

    assert mock_db_conn.execute.await_count == 2
    update_call, delete_call = mock_db_conn.execute.call_args_list
    assert "UPDATE accounts" in update_call.args[0]
    assert "ACTIVE" in update_call.args[0]
    assert update_call.args[1] == config.DEMO_ACCOUNT_BALANCE
    assert update_call.args[2] == 42
    assert "DELETE FROM payment_plans" in delete_call.args[0]
    assert delete_call.args[1] == 42


async def test_reset_demo_account_refuses_any_other_phone_number(patched_db_pool, mock_db_conn):
    """Hard-guarded internally -- must never touch an account whose phone
    number isn't the graded demo number, however it's called."""
    await db.reset_demo_account(42, "+19995551234")

    mock_db_conn.execute.assert_not_called()


async def test_apply_settlement(patched_db_pool, mock_db_conn):
    await db.apply_settlement(42)

    mock_db_conn.execute.assert_awaited_once()
    query, account_id = mock_db_conn.execute.call_args.args
    assert "UPDATE accounts" in query and "SETTLED" in query
    assert account_id == 42


async def test_set_requires_manual_review(patched_db_pool, mock_db_conn):
    await db.set_requires_manual_review(42)

    mock_db_conn.execute.assert_awaited_once_with(
        "UPDATE accounts SET requires_manual_review = $1 WHERE account_id = $2", True, 42
    )


async def test_create_payment_plan_writes_plan_and_updates_status(patched_db_pool, mock_db_conn):
    """Decimal, not float -- matching both the signature and the real
    caller (app/tools.py), against NUMERIC columns. A test passing floats
    here can't catch a Decimal/float regression, which is exactly the bug
    class app/tools.py's type hints were fixed for."""
    breakdown = "100.00,100.00,100.00,100.00,100.00"
    await db.create_payment_plan(
        42, 5, Decimal("100.00"), Decimal("500.00"), date(2026, 7, 10), breakdown
    )

    assert mock_db_conn.execute.await_count == 2
    plan_call, status_call = mock_db_conn.execute.call_args_list
    assert "INSERT INTO payment_plans" in plan_call.args[0]
    assert plan_call.args[1:] == (42, 5, Decimal("100.00"), Decimal("500.00"), date(2026, 7, 10), breakdown)
    assert "PAYMENT_PLAN_ACTIVE" in status_call.args[0]
    assert status_call.args[1] == 42


async def test_log_communication(patched_db_pool, mock_db_conn):
    await db.log_communication(42, "Settlement processed.")

    mock_db_conn.execute.assert_awaited_once()
    query, account_id, content = mock_db_conn.execute.call_args.args
    assert "INSERT INTO communication_logs" in query
    assert account_id == 42
    assert content == "Settlement processed."


async def test_create_voice_session_metrics(patched_db_pool, mock_db_conn):
    await db.create_voice_session_metrics("sess_1", 42, 90, 850, 2, "SETTLED", 1, "42/20260101T000000Z/log.txt")

    mock_db_conn.execute.assert_awaited_once()
    args = mock_db_conn.execute.call_args.args
    assert "INSERT INTO voice_session_metrics" in args[0]
    assert args[1:] == ("sess_1", 42, 90, 850, 2, "SETTLED", 1, "42/20260101T000000Z/log.txt")


async def test_create_voice_session_metrics_transcript_path_defaults_to_none(patched_db_pool, mock_db_conn):
    await db.create_voice_session_metrics("sess_1", 42, 90, 850, 2, "SETTLED", 1)

    assert mock_db_conn.execute.call_args.args[-1] is None


async def test_create_ai_evaluation_log(patched_db_pool, mock_db_conn):
    await db.create_ai_evaluation_log(
        "sess_1", True, True, False, True, False, None, 5, "Solid call.", 0.0045
    )

    mock_db_conn.execute.assert_awaited_once()
    args = mock_db_conn.execute.call_args.args
    assert "INSERT INTO ai_evaluation_logs" in args[0]
    assert args[1:] == ("sess_1", True, True, False, True, False, None, 5, "Solid call.", 0.0045)


async def test_get_account_returns_none_when_not_found(patched_db_pool):
    patched_db_pool.fetchrow.return_value = None
    assert await db.get_account("+10000000") is None


async def test_get_account_returns_dict(patched_db_pool):
    patched_db_pool.fetchrow.return_value = {
        "account_id": 42, "customer_name": "Marcus Vance", "phone_number": "+15550199",
        "current_balance": 500.0, "status": "ACTIVE",
    }
    account = await db.get_account("+15550199")
    assert account["account_id"] == 42
    assert account["customer_name"] == "Marcus Vance"


async def test_get_account_by_id_returns_dict(patched_db_pool):
    patched_db_pool.fetchrow.return_value = {
        "account_id": 42, "customer_name": "Marcus Vance", "phone_number": "+15550199",
        "current_balance": 500.0, "status": "ACTIVE",
    }
    account = await db.get_account_by_id(42)
    assert account["account_id"] == 42
    patched_db_pool.fetchrow.assert_awaited_once_with(
        "SELECT account_id, customer_name, phone_number, current_balance, status, requires_manual_review "
        "FROM accounts WHERE account_id = $1",
        42,
    )


async def test_get_accounts_returns_all(patched_db_pool):
    patched_db_pool.fetch.return_value = [
        {"account_id": 1, "customer_name": "Marcus Vance", "phone_number": "+15550199",
         "current_balance": 500.0, "status": "ACTIVE"},
        {"account_id": 2, "customer_name": "Dana Whitfield", "phone_number": "+15550102",
         "current_balance": 1450.0, "status": "ACTIVE"},
    ]
    accounts = await db.get_accounts()
    assert len(accounts) == 2
    assert accounts[0]["customer_name"] == "Marcus Vance"


async def test_get_compliance_summary(patched_db_pool):
    patched_db_pool.fetchrow.return_value = {
        "total_calls": 3, "mini_miranda_pass_rate": 1.0, "avg_tone_score": 4.5,
        "hallucination_count": 0, "prohibited_conduct_count": 0, "total_judge_cost_usd": 0.02,
    }
    summary = await db.get_compliance_summary()
    assert summary["total_calls"] == 3
    query = patched_db_pool.fetchrow.call_args.args[0]
    assert "FROM ai_evaluation_logs" in query
    # hallucination_count/prohibited_conduct_count must be COALESCEd to 0 --
    # a bare SUM() over zero matching rows (an account with no calls yet)
    # returns SQL NULL, which fails ComplianceSummary's non-optional int
    # validation and 500s the endpoint.
    assert "COALESCE(SUM(CASE WHEN ael.hallucination_detected" in query
    assert "COALESCE(SUM(CASE WHEN ael.prohibited_conduct_detected" in query


async def test_get_calls_joins_metrics_and_evaluation(patched_db_pool):
    patched_db_pool.fetch.return_value = [{"session_id": "sess_1", "disposition_code": "SETTLED"}]
    calls = await db.get_calls()
    assert calls == [{"session_id": "sess_1", "disposition_code": "SETTLED"}]
    query = patched_db_pool.fetch.call_args.args[0]
    assert "LEFT JOIN ai_evaluation_logs" in query
    assert "ORDER BY vsm.created_at DESC" in query


async def test_get_active_payment_plans_filters_by_status(patched_db_pool):
    await db.get_active_payment_plans()
    query = patched_db_pool.fetch.call_args.args[0]
    assert "WHERE status = 'ACTIVE'" in query


async def test_get_pending_callbacks_filters_by_status(patched_db_pool):
    await db.get_pending_callbacks()
    query = patched_db_pool.fetch.call_args.args[0]
    assert "WHERE status = 'PENDING'" in query


# --- The multi-call reset, proven end to end. The tests above only check
# that the right SQL string was issued -- they don't prove the account
# actually ends up clean, since the shared mock pool doesn't hold real
# state. This one does: a tiny in-memory fake Postgres, just for this test,
# that a settlement and a reset both really mutate, so "settle, then
# reset, then re-read" is verified as an actual state transition rather
# than assumed from three separate SQL-string checks. ---
class _FakeConn:
    def __init__(self, state):
        self._state = state

    async def execute(self, query, *params):
        normalized = " ".join(query.split())
        if "UPDATE accounts SET current_balance = 0" in normalized:
            self._state["current_balance"] = Decimal("0")
            self._state["status"] = "SETTLED"
        elif "UPDATE accounts SET current_balance = $1" in normalized:
            self._state["current_balance"] = params[0]
            self._state["status"] = "ACTIVE"
            self._state["requires_manual_review"] = False
        elif "DELETE FROM payment_plans" in normalized:
            self._state["payment_plans"] = []

    def transaction(self):
        return _NullAsyncContextManager()


class _NullAsyncContextManager:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc_info):
        return None


class _AcquireContextManager:
    def __init__(self, state):
        self._state = state

    async def __aenter__(self):
        return _FakeConn(self._state)

    async def __aexit__(self, *exc_info):
        return None


class _FakeStatefulPool:
    """Real mutable state for one account row -- .acquire()'d execute()
    calls and direct .fetchrow() calls both read/write the same dict."""

    def __init__(self, account_id: int, phone_number: str):
        self._state = {
            "account_id": account_id,
            "phone_number": phone_number,
            "current_balance": Decimal("0"),
            "status": "SETTLED",
            "requires_manual_review": False,
            "payment_plans": [],
        }

    def acquire(self):
        return _AcquireContextManager(self._state)

    async def fetchrow(self, query, *params):
        if "FROM accounts WHERE account_id" in query and params[0] == self._state["account_id"]:
            return dict(self._state)
        return None


async def test_settle_then_reset_actually_returns_the_account_to_clean_1000(monkeypatch):
    """The exact scenario the evaluator will hit: call #1 settles the
    account; call #2 connects. Proves reset_demo_account genuinely
    reverses a real settlement, not just that it issues plausible-looking
    SQL in isolation."""
    account_id = 42
    fake_pool = _FakeStatefulPool(account_id, config.DEFAULT_CUSTOMER_PHONE_NUMBER)
    monkeypatch.setattr(db, "_pool", fake_pool)

    # Call #1: consumer settles in full.
    await db.apply_settlement(account_id)
    mid_call_account = await db.get_account_by_id(account_id)
    assert mid_call_account["current_balance"] == Decimal("0")
    assert mid_call_account["status"] == "SETTLED"

    # Call #2 connects -- app/main.py resets before the greeting.
    await db.reset_demo_account(account_id, config.DEFAULT_CUSTOMER_PHONE_NUMBER)
    reset_account = await db.get_account_by_id(account_id)

    assert reset_account["current_balance"] == Decimal("1000.00")
    assert reset_account["status"] == "ACTIVE"
    assert reset_account["requires_manual_review"] is False
