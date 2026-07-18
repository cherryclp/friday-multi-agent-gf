import edge_tts
import asyncio
import tempfile
import os
from playsound import playsound

VOICES = [
    "en-US-AvaNeural",
    "en-US-JennyNeural",
    "en-US-MichelleNeural",
    "en-US-MonicaNeural",
    "en-US-EmmaNeural",
    "en-US-SaraNeural",
    "en-GB-SoniaNeural",
    "en-GB-LibbyNeural",
]

TEXT = "this is so long because I want to test the voices and make sure they are working properly."

async def test_voice(voice, retries=3):
    for attempt in range(retries):
        try:
            print(f"Playing: {voice}")
            communicate = edge_tts.Communicate(TEXT, voice)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                tmp_path = f.name
            await communicate.save(tmp_path)
            playsound(tmp_path)
            os.unlink(tmp_path)
            return
        except Exception as e:
            print(f"  Attempt {attempt+1} failed: {e}, retrying...")
            await asyncio.sleep(2)
    print(f"  Skipping {voice} after {retries} failed attempts")

async def main():
    for voice in VOICES:
        await test_voice(voice)
        await asyncio.sleep(1)

asyncio.run(main())