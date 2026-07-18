"""Corafone Voice Gateway: browser-facing WebSocket route (``/ws/stream``).
Relays mic audio to Deepgram's Voice Agent and audio/control packets back,
via app/voice_agent.py. Business logic lives in app/tools.py, per-call state
in app/session.py, settings in app/config.py."""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from deepgram.agent.v1.types import AgentV1InjectUserMessage

from . import config, db
from .dashboard_api import router as dashboard_router
from .session import CallSession
from .voice_agent import initialize_agent_connection, teardown_session

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("corafone")

# `websockets` logs every raw frame at DEBUG, which buries our own log
# lines -- keep it at WARNING regardless of our own LOG_LEVEL.
logging.getLogger("websockets").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(title=config.APP_TITLE, lifespan=lifespan)

# Dashboard dev server runs on a different origin/port -- needs CORS to call
# the read-only + scenario-runner API below.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.DASHBOARD_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(dashboard_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "healthy"})


async def _handle_audio_frame(audio_data: bytes, session: CallSession) -> None:
    await session.agent_connection.send_media(audio_data)


async def _handle_text_frame(raw_text: str, session: CallSession) -> None:
    """Handles the `mock_transcript` simulation path, forwarded as Deepgram's
    native "simulate spoken input" message."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return

    if data.get("type") != "mock_transcript":
        return

    await session.agent_connection.send_inject_user_message(
        AgentV1InjectUserMessage(content=data.get("text"))
    )


@app.websocket(config.WS_ROUTE_PATH)
async def handle_audio_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Browser client voice channel connected.")

    session = CallSession(websocket=websocket)

    try:
        phone_number = websocket.query_params.get("phone_number", config.DEFAULT_CUSTOMER_PHONE_NUMBER)
        account = await db.get_account(phone_number)
        if account is None:
            logger.error("No account found for phone number %r -- closing.", phone_number)
            await websocket.send_text(json.dumps({"type": "error", "message": "Unknown account."}))
            await websocket.close()
            return

        session.account_id = account["account_id"]
        session.customer_name = account["customer_name"]

        # Idempotent demo reset, before the greeting -- see db.reset_demo_account.
        await db.reset_demo_account(session.account_id, phone_number)
        account = await db.get_account_by_id(session.account_id)
        session.account_balance = account["current_balance"]  # Decimal, straight from asyncpg

        await initialize_agent_connection(session)
        logger.info("Awaiting incoming browser microphone stream packets...")

        while True:
            message = await websocket.receive()

            # A disconnect arrives as a plain message, not an exception --
            # handle it here instead of via the broad except block below.
            if message["type"] == "websocket.disconnect":
                logger.info("Browser client disconnected (code %s).", message.get("code"))
                break

            if message.get("bytes") is not None:
                await _handle_audio_frame(message["bytes"], session)
            elif message.get("text") is not None:
                await _handle_text_frame(message["text"], session)

    except WebSocketDisconnect:
        logger.info("Browser client disconnected cleanly.")
    except Exception:
        logger.exception("Gateway connection error.")
    finally:
        await teardown_session(session)
