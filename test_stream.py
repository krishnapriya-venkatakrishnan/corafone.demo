import asyncio
import websockets
import os


async def test_voice_pipeline():
    uri = "ws://127.0.0.1:8000/ws/stream"
    print("Attempting connection to local voice gateway...")

    try:
        async with websockets.connect(uri) as websocket:
            print(
                "Connected to gateway. Beginning live binary streaming mock simulation..."
            )

            for second in range(10):
                # Generate a valid 16kHz alternating signal structure (simulating PCM white noise)
                # This ensures the streaming node processes it as valid telephony format data
                mock_audio_pcm_chunk = bytes([0x55, 0xAA] * 8000)

                await websocket.send(mock_audio_pcm_chunk)
                await asyncio.sleep(1)
                print(f"Streamed audio block frame: {second + 1}/10 seconds sent.")

            print("Finished streaming voice telemetry data.")

    except Exception as e:
        print(f"Local stream test failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_voice_pipeline())
