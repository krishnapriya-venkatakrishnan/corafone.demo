"""Backends for the two agent tools (settlement, payment plans) and their
dispatch from Deepgram's FunctionCallRequest.

Each `process_*`/`schedule_*`/`create_*` function persists its outcome to
Supabase (app/db.py). The `_execute_*_tool_call` wrappers add idempotency
(run at most once per call) on top. Account identity (`session.account_id`)
is always resolved server-side at call start, never supplied by the LLM --
a phone call gives the model no reliable way to know its own database id.
"""

import json
import logging
import uuid
from datetime import date

from deepgram.agent.v1.types import AgentV1SendFunctionCallResponse

from . import db
from .session import CallSession, append_call_log

logger = logging.getLogger("corafone")


# --- Settlement ---
async def process_account_settlement(account_id: int, amount: float) -> dict:
    """Charges the account for an agreed lump-sum settlement."""
    logger.info("Ledger: charging account %s for $%.2f...", account_id, amount)
    await db.apply_settlement(account_id)
    transaction_id = f"tx_corafone_{uuid.uuid4().hex[:12]}"
    await db.log_communication(
        account_id, f"Settlement processed: {transaction_id}, ${amount:.2f} charged."
    )
    logger.info("Ledger: SUCCESS -- $%.2f deducted, account %s marked SETTLED.", amount, account_id)
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "amount_charged": amount,
        "balance_remaining": 0.00,
        "account_status": "CLOSED_SETTLED",
    }


async def _execute_settlement_tool_call(args: dict, session: CallSession) -> dict:
    async with session.settlement_lock:
        if session.settlement_settled:
            logger.info("Settlement already processed this call.")
            return {
                "status": "already_settled",
                "transaction_id": session.settlement_transaction_id,
                "amount_charged": session.settlement_amount,
                "balance_remaining": 0.00,
                "account_status": "CLOSED_SETTLED",
            }

        result = await process_account_settlement(session.account_id, args["amount"])
        session.settlement_settled = True
        session.settlement_transaction_id = result["transaction_id"]
        session.settlement_amount = result["amount_charged"]
        append_call_log(
            session, "Billing",
            f"Settlement processed: {result['transaction_id']}, ${result['amount_charged']:.2f} charged.",
        )
        return result


# --- Payment plans ---
async def create_installment_payment_plan(
    account_id: int, num_installments: int, amount_per_installment: float, start_date: str
) -> dict:
    """Sets up a recurring installment plan in the billing system. `start_date`
    is an absolute YYYY-MM-DD string (the LLM resolves it from today's date +
    the customer's own words -- see config.build_system_prompt rule 4)."""
    parsed_start_date = date.fromisoformat(start_date)
    logger.info(
        "Billing: creating %d-installment plan of $%.2f/mo (starting %s) for account %s...",
        num_installments, amount_per_installment, parsed_start_date, account_id,
    )
    total_amount = round(num_installments * amount_per_installment, 2)
    await db.create_payment_plan(
        account_id, num_installments, amount_per_installment, total_amount, parsed_start_date
    )
    plan_id = f"plan_corafone_{uuid.uuid4().hex[:12]}"
    await db.log_communication(
        account_id,
        f"Payment plan created: {plan_id}, ${total_amount:.2f} total across "
        f"{num_installments} payments starting {parsed_start_date.isoformat()}.",
    )
    logger.info(
        "Billing: SUCCESS -- plan %s created, $%.2f total across %d payments starting %s.",
        plan_id, total_amount, num_installments, parsed_start_date,
    )
    return {
        "status": "plan_created",
        "plan_id": plan_id,
        "num_installments": num_installments,
        "amount_per_installment": amount_per_installment,
        "total_amount": total_amount,
        "start_date": parsed_start_date.isoformat(),
        "account_status": "PAYMENT_PLAN_ACTIVE",
    }


async def _execute_payment_plan_tool_call(args: dict, session: CallSession) -> dict:
    async with session.payment_plan_lock:
        if session.payment_plan_created:
            logger.info("Payment plan already created this call.")
            return {
                "status": "already_created",
                "plan_id": session.payment_plan_id,
                "num_installments": session.payment_plan_installments,
                "amount_per_installment": session.payment_plan_amount_per_installment,
                "start_date": session.payment_plan_start_date,
            }

        result = await create_installment_payment_plan(
            session.account_id,
            args["num_installments"],
            args["amount_per_installment"],
            args["start_date"],
        )
        session.payment_plan_created = True
        session.payment_plan_id = result["plan_id"]
        session.payment_plan_installments = result["num_installments"]
        session.payment_plan_amount_per_installment = result["amount_per_installment"]
        session.payment_plan_start_date = result["start_date"]
        append_call_log(
            session, "Billing",
            f"Payment plan created: {result['plan_id']}, ${result['total_amount']:.2f} total across "
            f"{result['num_installments']} payments starting {result['start_date']}.",
        )
        return result


# --- Dispatch ---
_FUNCTION_CALL_HANDLERS = {
    "process_account_settlement": _execute_settlement_tool_call,
    "offer_payment_plan": _execute_payment_plan_tool_call,
}


async def handle_function_call_request(message, session: CallSession) -> None:
    """Executes each client_side function Deepgram asked for and reports
    the result back over the same connection."""
    for function_call in message.functions:
        handler = _FUNCTION_CALL_HANDLERS.get(function_call.name)
        if handler is None:
            logger.warning("Ignoring unknown function call request: %s", function_call.name)
            continue

        args = json.loads(function_call.arguments)
        try:
            result = await handler(args, session)
        except Exception:
            logger.exception("Tool call '%s' failed.", function_call.name)
            session.error_count += 1
            result = {
                "status": "error",
                "message": "That didn't go through due to a system issue -- let's try again.",
            }

        await session.agent_connection.send_function_call_response(
            AgentV1SendFunctionCallResponse(
                id=function_call.id,
                name=function_call.name,
                content=json.dumps(result),
            )
        )
