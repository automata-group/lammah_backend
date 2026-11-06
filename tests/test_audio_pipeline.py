import os
import sys
import subprocess
from dotenv import load_dotenv

# Load env from backend/.env
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)
sys.path.insert(0, os.path.dirname(__file__))

from app.services.audio_service import (
    get_audio_duration_seconds,
    _transcribe_with_groq,
    _transcribe_with_gemini,
    _transcribe_single_segment,
    transcribe_audio
)


def test_audio_duration():
    # Generate 5-second sine wave mp3
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_dur.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:a", "libmp3lame", test_file
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        dur = get_audio_duration_seconds(test_file)
        print(f"Detected duration: {dur}s")
        assert 4.0 <= dur <= 6.0
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


def test_transcribe_with_gemini_fallback():
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_gemini.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=800:duration=2",
            "-c:a", "libmp3lame", test_file
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = _transcribe_with_gemini(test_file, "mp3")
        print(f"Gemini ASR Output: {res}")
        # Gemini should either return detected sounds or handle gracefully without throwing
        assert res is None or isinstance(res, str)
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


def test_transcribe_single_segment():
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_seg.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:a", "libmp3lame", test_file
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        res = _transcribe_single_segment(test_file, "mp3")
        print(f"Single segment result: {res}")
        assert isinstance(res, str)
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


if __name__ == "__main__":
    test_audio_duration()
    test_transcribe_with_gemini_fallback()
    test_transcribe_single_segment()
    print("✅ All audio pipeline tests PASSED!")
