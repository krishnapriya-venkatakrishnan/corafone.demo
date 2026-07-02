import asyncio
import websockets


async def receive_audio_responses(websocket):
    """Listens for returning audio bytes from Cora."""
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                print(
                    f"[Client] Received {len(message)} audio bytes back from Cora's voice engine!"
                )
    except websockets.exceptions.ConnectionClosed:
        pass


async def test_voice_pipeline():
    uri = "ws://127.0.0.1:8000/ws/stream"
    print("Attempting connection to local voice gateway...")

    try:
        async with websockets.connect(uri) as websocket:
            print(
                "Connected to gateway. Beginning live bidirectional streaming simulation..."
            )

            # Start a background task to constantly listen for Cora speaking back to us
            listener_task = asyncio.create_task(receive_audio_responses(websocket))

            # Stream mock user audio frames for 10 seconds
            for second in range(10):
                mock_audio_pcm_chunk = bytes([0x55, 0xAA] * 8000)
                await websocket.send(mock_audio_pcm_chunk)
                await asyncio.sleep(1)
                print(f"Streamed user audio block frame: {second + 1}/10 seconds sent.")

            print(
                "Finished sending user telemetry data. Waiting briefly for trailing audio..."
            )
            await asyncio.sleep(2)
            listener_task.cancel()

    except Exception as e:
        print(f"Local stream test failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_voice_pipeline())
