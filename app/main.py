import os
import asyncio
from dotenv import load_dotenv

# CRITICAL: Load environment variables BEFORE importing Deepgram
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

app = FastAPI(title="Corafone Voice Gateway")

# Initialize Deepgram using the correct keyword argument syntax
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
deepgram_client = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)


@app.get("/health")
async def health_check():
    return JSONResponse(
        status_code=200, content={"status": "healthy", "gateway": "operational"}
    )


@app.websocket("/ws/stream")
async def handle_audio_stream(websocket: WebSocket):
    await websocket.accept()
    print("Telephony or web client connected to streaming socket gateway.")

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
                        print(f"Deepgram Real-time Transcript: {sentence}")

            def on_error(error, **kwargs):
                print(f"Deepgram streaming error: {error}")

            # Wire up event listeners using the modern EventType interface
            dg_connection.on(EventType.MESSAGE, on_message)
            dg_connection.on(EventType.ERROR, on_error)

            print("Bidirectional Deepgram audio pipeline successfully initialized.")

            # Continuous non-blocking audio byte broker loop
            while True:
                # Receive raw PCM / Mu-Law binary chunks streaming from the frontend or telephony provider
                data = await websocket.receive_bytes()

                # Instantly stream the media bytes upstream over Deepgram's persistent pipe
                await dg_connection.send_media(data)

    except WebSocketDisconnect:
        print("Telephony web client disconnected from gateway cleanly.")
    except Exception as e:
        print(f"Voice gateway exception encountered in processing stream: {e}")
