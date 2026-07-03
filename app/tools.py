"""Mock backends for the three agent tools (settlement, callback scheduling,
payment plans) and their dispatch from Deepgram's FunctionCallRequest.

Each `process_*`/`schedule_*`/`create_*` function below is a stand-in for a
real database/payment-gateway/calendar call -- this is where that real
integration lands. The `_execute_*_tool_call` wrappers add idempotency (run
at most once per call) on top.
"""

import asyncio
import json
import logging
import uuid

from deepgram.agent.v1.types import AgentV1SendFunctionCallResponse

from . import config
from .session import CallSession

logger = logging.getLogger("corafone")


# --- Settlement ---
async def process_account_settlement(account_id: str, amount: float) -> dict:
    """Charges the account for an agreed lump-sum settlement."""
    logger.info("Ledger: charging account %s for $%.2f...", account_id, amount)
    await asyncio.sleep(config.MOCK_LEDGER_LATENCY_SECONDS)
    transaction_id = f"tx_corafone_{uuid.uuid4().hex[:12]}"
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

        result = await process_account_settlement(args["account_id"], args["amount"])
        session.settlement_settled = True
        session.settlement_transaction_id = result["transaction_id"]
        session.settlement_amount = result["amount_charged"]
        return result


# --- Callback scheduling ---
async def schedule_followup_callback(requested_time_description: str) -> dict:
    """Books a follow-up call into the calendar/dialer system."""
    logger.info("Scheduler: booking follow-up callback for '%s'...", requested_time_description)
    await asyncio.sleep(config.MOCK_SCHEDULING_LATENCY_SECONDS)
    callback_id = f"cb_corafone_{uuid.uuid4().hex[:12]}"
    logger.info("Scheduler: SUCCESS -- callback %s booked for '%s'.", callback_id, requested_time_description)
    return {
        "status": "scheduled",
        "callback_id": callback_id,
        "requested_time": requested_time_description,
    }


async def _execute_schedule_callback_tool_call(args: dict, session: CallSession) -> dict:
    async with session.callback_lock:
        if session.callback_scheduled:
            logger.info("Callback already scheduled this call.")
            return {
                "status": "already_scheduled",
                "callback_id": session.callback_id,
                "requested_time": session.callback_requested_time,
            }

        result = await schedule_followup_callback(args["requested_datetime_description"])
        session.callback_scheduled = True
        session.callback_id = result["callback_id"]
        session.callback_requested_time = result["requested_time"]
        return result


# --- Payment plans ---
async def create_installment_payment_plan(
    account_id: str, num_installments: int, amount_per_installment: float, start_date_description: str
) -> dict:
    """Sets up a recurring installment plan in the billing system."""
    logger.info(
        "Billing: creating %d-installment plan of $%.2f/mo (starting %s) for account %s...",
        num_installments, amount_per_installment, start_date_description, account_id,
    )
    await asyncio.sleep(config.MOCK_LEDGER_LATENCY_SECONDS)
    plan_id = f"plan_corafone_{uuid.uuid4().hex[:12]}"
    total_amount = round(num_installments * amount_per_installment, 2)
    logger.info(
        "Billing: SUCCESS -- plan %s created, $%.2f total across %d payments starting %s.",
        plan_id, total_amount, num_installments, start_date_description,
    )
    return {
        "status": "plan_created",
        "plan_id": plan_id,
        "num_installments": num_installments,
        "amount_per_installment": amount_per_installment,
        "total_amount": total_amount,
        "start_date": start_date_description,
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
            args["account_id"],
            args["num_installments"],
            args["amount_per_installment"],
            args["start_date_description"],
        )
        session.payment_plan_created = True
        session.payment_plan_id = result["plan_id"]
        session.payment_plan_installments = result["num_installments"]
        session.payment_plan_amount_per_installment = result["amount_per_installment"]
        session.payment_plan_start_date = result["start_date"]
        return result


# --- Dispatch ---
_FUNCTION_CALL_HANDLERS = {
    "process_account_settlement": _execute_settlement_tool_call,
    "schedule_callback": _execute_schedule_callback_tool_call,
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
        result = await handler(args, session)

        await session.agent_connection.send_function_call_response(
            AgentV1SendFunctionCallResponse(
                id=function_call.id,
                name=function_call.name,
                content=json.dumps(result),
            )
        )
