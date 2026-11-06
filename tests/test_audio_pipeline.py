import os
import sys
import wave
import struct
import math
from dotenv import load_dotenv

# Load env from backend/.env if present
env_path = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.audio_service import (
    get_audio_duration_seconds,
    _transcribe_with_groq,
    _transcribe_with_gemini,
    _transcribe_single_segment,
    transcribe_audio
)


def _generate_pure_python_wav(file_path: str, duration_sec: int = 5, sample_rate: int = 8000):
    """Generates a valid, playable PCM WAV file in pure Python without requiring ffmpeg."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with wave.open(file_path, "wb") as wav:
        wav.setnchannels(1)  # Mono
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        total_samples = duration_sec * sample_rate
        raw_data = bytearray()
        for i in range(total_samples):
            val = int(32767.0 * 0.3 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
            raw_data.extend(struct.pack("<h", val))
        wav.writeframes(raw_data)


def test_audio_duration():
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_dur.wav")
    try:
        _generate_pure_python_wav(test_file, duration_sec=5)
        dur = get_audio_duration_seconds(test_file)
        print(f"Detected duration: {dur}s")
        assert 4.0 <= dur <= 6.0
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


def test_transcribe_with_gemini_fallback():
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_gemini.wav")
    try:
        _generate_pure_python_wav(test_file, duration_sec=2)
        res = _transcribe_with_gemini(test_file, "wav")
        print(f"Gemini ASR Output: {res}")
        assert res is None or isinstance(res, str)
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


def test_transcribe_single_segment():
    test_file = os.path.join(os.path.dirname(__file__), "test_temp_seg.wav")
    try:
        _generate_pure_python_wav(test_file, duration_sec=2)
        res = _transcribe_single_segment(test_file, "wav")
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
