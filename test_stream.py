import asyncio
import json
import websockets


async def receive_audio_responses(websocket):
    """Listens for returning audio bytes from Cora."""
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                print(f"[Client] Received {len(message)} audio voice bytes from Cora.")
    except websockets.exceptions.ConnectionClosed:
        pass


async def test_voice_pipeline():
    uri = "ws://127.0.0.1:8000/ws/stream"
    print("Attempting connection to local agentic voice gateway...")

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected. Running simulation...")
            listener_task = asyncio.create_task(receive_audio_responses(websocket))

            # Wait 4 seconds for Cora to finish greeting us
            await asyncio.sleep(4)

            print(
                "\n[Simulation Trigger] Injecting mock text payload into gateway: 'I accept the $300 settlement. Go ahead and charge it.'"
            )

            # Formulate an explicit structural payload to force the backend OpenAI route to process the statement
            # In a live setup, the server's on_message hook appends this string directly when Deepgram triggers.
            # We send a tiny text trigger packet to simulate that transcription completion.
            trigger_packet = {
                "type": "mock_transcript",
                "text": "I accept the $300 settlement. Go ahead and charge it.",
            }
            await websocket.send(json.dumps(trigger_packet))

            # CRITICAL: Sleep here to keep the connection alive to listen to Cora's execution reply!
            await asyncio.sleep(6)

            print("\nSimulation run completed successfully.")
            listener_task.cancel()

    except Exception as e:
        print(f"Local stream test failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_voice_pipeline())
