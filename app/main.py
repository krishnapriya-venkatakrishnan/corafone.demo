import os
import json
import asyncio
import httpx
from dotenv import load_dotenv

# CRITICAL: Load environment variables BEFORE importing Deepgram
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from openai import AsyncOpenAI

app = FastAPI(title="Corafone Voice Gateway - Phase 6 Agentic Core")

# Initialize Client Gateways
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

deepgram_client = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Rigid Business Rules & Persona Definition for our Live Voice Agent
SYSTEM_PROMPT = """
You are Cora, an automated outbound voice collection agent for Corafone Financial. 
Your tone must remain highly professional, respectful, and firm but empathetic.

CRITICAL RULES:
1. On your absolute first turn, you MUST state the legal Mini-Miranda disclosure exactly: 
   "This is an attempt to collect a debt by a debt collector. Any information obtained will be used for that purpose."
2. The customer, Marcus Vance, owes a balance of $500.00.
3. You are authorized to offer a settlement discount up to a maximum of 40% ($300.00 settlement total). 
   You must NEVER go lower than a 40% discount.
4. Keep your responses short, natural, and concise (under 2-3 sentences max).
5. IF the customer explicitly agrees to pay the settled balance (minimum $300.00), you MUST execute the `process_account_settlement` tool immediately to resolve their debt. Do not merely state that you are doing it; call the function.
"""

# Define the JSON schema for our Agentic Settlement Tool
SETTLEMENT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "process_account_settlement",
        "description": "Executes an immediate collection settlement deduction against the user account database balance.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "The unique system identifier for the user account.",
                },
                "amount": {
                    "type": "number",
                    "description": "The exact settlement dollar total agreed upon (Minimum 300.00).",
                },
            },
            "required": ["account_id", "amount"],
        },
    },
}


async def process_account_settlement(account_id: str, amount: float):
    """
    Simulates a secure database transaction layer ledger event.
    In production, this blocks out to Supabase and charges the Stripe Payment gateway.
    """
    print(f"\n[DB Ledger Tool] Accessing account transaction logs for: {account_id}...")
    await asyncio.sleep(0.8)  # Simulates system latency round-trip time
    print(
        f"[DB Ledger Tool] SUCCESS: Deducted ${amount:.2f}. Balance marked fully SETTLED.\n"
    )
    return {
        "status": "success",
        "transaction_id": "tx_corafone_mock_982471",
        "amount_charged": amount,
        "balance_remaining": 0.00,
        "account_status": "CLOSED_SETTLED",
    }


async def stream_text_to_speech(text_chunk: str, websocket: WebSocket):
    """
    Takes a text fragment, sends it to Deepgram Aura TTS,
    and sends the raw audio bytes directly back to the caller.
    """
    # Deepgram Aura TTS streaming endpoint
    tts_url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    # Requesting linear16 PCM at 8000Hz to perfectly match telephony standards
    payload = {"text": text_chunk}
    params = {"encoding": "linear16", "sample_rate": 8000}

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", tts_url, headers=headers, json=payload, params=params
            ) as response:
                if response.status_code == 200:
                    async for audio_chunk in response.aiter_bytes(chunk_size=1024):
                        # Stream the raw audio bytes directly down the WebSocket to the caller!
                        await websocket.send_bytes(audio_chunk)
                else:
                    print(f"[TTS] Error from Deepgram Aura: {response.status_code}")
    except Exception as e:
        print(f"[TTS] Exception during audio generation: {e}")


async def generate_agent_response(
    conversation_history, websocket: WebSocket, settlement_state, settlement_lock
):
    print("\n[Brain] Spinning up OpenAI stream engine...")
    try:
        # We pass our settlement tools schema array directly into the collection parameters
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}]
            + conversation_history,
            tools=[SETTLEMENT_TOOL_SCHEMA],
            tool_choice="auto",
        )

        response_message = response.choices[0].message

        # Check if Cora decided she needs to execute a tool action
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "process_account_settlement":
                    # Parse argument parameters out of the agentic structure safely
                    args = json.loads(tool_call.function.arguments)

                    # Deepgram may finalize one spoken instruction as multiple
                    # transcripts (e.g. a mid-sentence pause), each spawning its
                    # own concurrent turn. The lock + flag below guarantee only
                    # the first turn to reach this point can ever actually charge
                    # the account for a given call — every other turn just gets
                    # told the account is already settled.
                    async with settlement_lock:
                        if settlement_state["settled"]:
                            print(
                                "[DB Ledger Tool] Settlement already processed this call — skipping duplicate charge."
                            )
                            result = {
                                "status": "already_settled",
                                "transaction_id": settlement_state["transaction_id"],
                                "amount_charged": settlement_state["amount"],
                                "balance_remaining": 0.00,
                                "account_status": "CLOSED_SETTLED",
                            }
                        else:
                            result = await process_account_settlement(
                                args["account_id"], args["amount"]
                            )
                            settlement_state["settled"] = True
                            settlement_state["transaction_id"] = result["transaction_id"]
                            settlement_state["amount"] = result["amount_charged"]

                    # Update conversation memory to reflect the tool call execution sequence
                    conversation_history.append(response_message)
                    conversation_history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": "process_account_settlement",
                            "content": json.dumps(result),
                        }
                    )

                    # Recurse instantly back into OpenAI so Cora can read the success token and verbalize it
                    return await generate_agent_response(
                        conversation_history, websocket, settlement_state, settlement_lock
                    )

        # Otherwise, process standard conversational response text
        reply_text = response_message.content
        if reply_text:
            print(f"[Brain] Cora Voice Response: {reply_text}")
            await stream_text_to_speech(reply_text, websocket)
            return reply_text

    except Exception as e:
        print(f"\n[Brain] Error in agentic response loop: {e}")
        return None


@app.get("/health")
async def health_check():
    return JSONResponse(
        status_code=200, content={"status": "healthy", "agentic_tools": "active"}
    )


@app.websocket("/ws/stream")
async def handle_audio_stream(websocket: WebSocket):
    await websocket.accept()
    print("Browser client voice channel connected successfully.")
    # Persistent conversational session memory array
    conversation_history = []

    # Store reference to the active socket so our async handlers can access it safely
    current_session_socket = websocket

    # Guards against the settlement tool firing more than once per call, even
    # if Deepgram finalizes the customer's agreement as multiple overlapping
    # transcripts that each spawn their own concurrent turn.
    settlement_lock = asyncio.Lock()
    settlement_state = {"settled": False, "transaction_id": None, "amount": None}

    # Track the active Deepgram connection and its context manager state
    dg_context = None
    dg_connection = None
    dg_listen_task = None

    # Unified conversational brain router
    async def process_conversational_turn(user_text: str):
        print(f"\n[Processing Turn] User said: '{user_text}'")
        conversation_history.append({"role": "user", "content": user_text})

        agent_reply = await generate_agent_response(
            conversation_history, current_session_socket, settlement_state, settlement_lock
        )
        if agent_reply:
            conversation_history.append({"role": "assistant", "content": agent_reply})

    # Deepgram trigger listener
    def on_message(message, **kwargs):
        if hasattr(message, "channel") and hasattr(message.channel, "alternatives"):
            sentence = message.channel.alternatives[0].transcript
            if len(sentence.strip()) > 0:
                print(f"\n[STT Voice Capture] Deepgram finalized: '{sentence}'")
                # This callback fires from inside dg_connection.start_listening(),
                # which runs on this same event loop, so a plain create_task suffices.
                asyncio.create_task(process_conversational_turn(sentence))

    try:
        # Step 1: Boot Cora's greeting statement safely right out of the gate
        print("\n[Brain] Spawning compliant greeting introduction...")
        conversation_history.append(
            {
                "role": "user",
                "content": "[System Signal: Call Connected. Greet the customer and state your disclosure.]",
            }
        )
        agent_reply = await generate_agent_response(
            conversation_history, current_session_socket, settlement_state, settlement_lock
        )
        if agent_reply:
            conversation_history.append({"role": "assistant", "content": agent_reply})

        print("Awaiting incoming browser microphone stream packets...")

        # Continuous network loop
        while True:
            message = await websocket.receive()

            # Catch raw client microphone binary data blocks
            if message.get("bytes") is not None:
                audio_data = message["bytes"]

                # LAZY INITIALIZATION: Securely hook into the context manager lifecycle
                if dg_connection is None:
                    print(
                        "First user voice byte captured. Initializing Deepgram pipeline..."
                    )

                    # Explicitly tell Deepgram exactly what kind of audio data the frontend is streaming
                    dg_context = deepgram_client.listen.v1.connect(
                        model="nova-2",
                        language="en-US",
                        smart_format=True,
                        interim_results=False,
                        # Add these parameters to tell Deepgram to expect raw PCM at 8kHz mono
                        encoding="linear16",
                        sample_rate=8000,
                        channels=1,
                    )
                    # Manually enter the asynchronous generator context safely
                    dg_connection = await dg_context.__aenter__()

                    dg_connection.on(EventType.MESSAGE, on_message)
                    dg_connection.on(
                        EventType.ERROR,
                        lambda e, **k: print(f"Deepgram stream error: {e}"),
                    )

                    # dg_connection.on(...) only registers callbacks — nothing
                    # actually reads off the Deepgram socket and emits MESSAGE
                    # events until start_listening() is running. Without this,
                    # audio reaches Deepgram fine but transcripts never come back.
                    dg_listen_task = asyncio.create_task(
                        dg_connection.start_listening()
                    )
                    print("Deepgram streaming channel authenticated.")

                # Safely pass bytes to our live channel
                await dg_connection.send_media(audio_data)

            # Catch simulation text clicks from the interface
            elif message.get("text") is not None:
                try:
                    data = json.loads(message["text"])
                    if data.get("type") == "mock_transcript":
                        await process_conversational_turn(data.get("text"))
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        print("Browser client disconnected cleanly.")
    except Exception as e:
        print(f"Gateway connection exception caught: {e}")
    finally:
        # Guarantee network cleanup when the user exits the browser tab
        if dg_listen_task is not None:
            dg_listen_task.cancel()
        if dg_context is not None:
            print("Shutting down active Deepgram channels.")
            await dg_context.__aexit__(None, None, None)
