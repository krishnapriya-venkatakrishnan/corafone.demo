import os
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

app = FastAPI(title="Corafone Voice Gateway - Phase 4")

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
   You must NEVER go lower than a 40% discount. If they demand a higher discount, firmly state that $300.00 is the absolute minimum legal adjustment allowed today.
4. Keep your responses short, natural, and concise (under 2-3 sentences max) because you are speaking over a live voice connection.
"""


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


async def generate_agent_response(conversation_history, websocket: WebSocket):
    print("\n[Brain] Spinning up OpenAI stream engine...")
    try:
        response_stream = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}]
            + conversation_history,
            stream=True,
        )

        full_reply = ""
        current_sentence = ""
        print("[Brain] Cora Response Text Stream: ", end="", flush=True)

        async for chunk in response_stream:
            text_chunk = chunk.choices[0].delta.content
            if text_chunk:
                print(text_chunk, end="", flush=True)
                full_reply += text_chunk
                current_sentence += text_chunk

                # To minimize latency, we don't wait for the full response.
                # We send chunks to TTS as soon as a clause or sentence completes (, . ! ?)
                if any(char in text_chunk for char in [".", "!", "?", ","]):
                    await stream_text_to_speech(current_sentence, websocket)
                    current_sentence = ""

        # Catch any trailing text
        if current_sentence.strip():
            await stream_text_to_speech(current_sentence, websocket)

        print("\n[Brain] Stream complete. Appending reply to session memory.\n")
        return full_reply

    except Exception as e:
        print(f"\n[Brain] Error generating LLM voice response: {e}")
        return None


@app.get("/health")
async def health_check():
    return JSONResponse(
        status_code=200,
        content={"status": "healthy", "gateway": "operational", "audio_out": "active"},
    )


@app.websocket("/ws/stream")
async def handle_audio_stream(websocket: WebSocket):
    await websocket.accept()
    print("Telephony or web client connected to streaming socket gateway.")

    # Persistent conversational session memory array
    conversation_history = []

    try:
        # Establish a modern context-managed async websocket to Deepgram
        async with deepgram_client.listen.v1.connect(
            model="nova-2", language="en-US", smart_format=True, interim_results=False
        ) as dg_connection:
            # Callback definition for incoming text payloads from Deepgram
            def on_message(message, **kwargs):
                if hasattr(message, "channel") and hasattr(
                    message.channel, "alternatives"
                ):
                    sentence = message.channel.alternatives[0].transcript
                    if len(sentence.strip()) > 0:
                        print(f"\n[STT] Deepgram finalized: '{sentence}'")

                        # Save what the consumer said straight into our session history
                        conversation_history.append(
                            {"role": "user", "content": sentence}
                        )

                        # Schedule the OpenAI conversational processing background task non-blockingly
                        asyncio.create_task(trigger_llm_reply())

            async def trigger_llm_reply():
                # Call our conversational brain
                agent_reply = await generate_agent_response(
                    conversation_history, websocket
                )
                if agent_reply:
                    # Save what Cora said back into memory so context carries over to the next turn
                    conversation_history.append(
                        {"role": "assistant", "content": agent_reply}
                    )

            def on_error(error, **kwargs):
                print(f"Deepgram streaming error: {error}")

            # Wire up event listeners using the modern EventType interface
            dg_connection.on(EventType.MESSAGE, on_message)
            dg_connection.on(EventType.ERROR, on_error)

            print("Bidirectional Deepgram audio pipeline successfully initialized.")

            # Trigger Cora's initial Mini-Miranda greeting call introduction
            conversation_history.append(
                {
                    "role": "user",
                    "content": "[System Signal: Call Connected. Greet the customer and state your disclosure.]",
                }
            )
            await trigger_llm_reply()

            while True:
                # Receive raw PCM / Mu-Law binary chunks streaming from the frontend or telephony provider
                data = await websocket.receive_bytes()

                # Instantly stream the media bytes upstream over Deepgram's persistent pipe
                await dg_connection.send_media(data)

    except WebSocketDisconnect:
        print("Telephony web client disconnected from gateway cleanly.")
    except Exception as e:
        # Check if the exception string represents a clean standard websocket closure
        if "1000 (OK)" in str(e):
            print(
                "Telephony client closed connection normally (1000 OK). Clean teardown executed."
            )
        else:
            print(f"Voice gateway exception encountered in processing stream: {e}")
