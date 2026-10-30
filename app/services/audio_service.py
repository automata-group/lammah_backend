import os
import base64
import glob
import subprocess
import requests
from typing import Optional, List
from app.core.config import settings
from app.core.logger import logger

# Initialize Transcription Client placeholder to maintain compatibility
client = None
is_openrouter = True


def get_audio_duration_seconds(file_path: str) -> float:
    """Returns duration in seconds using ffprobe, or 0.0 on failure."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        return float(out)
    except Exception as e:
        logger.warning(f"Could not read duration with ffprobe for {file_path}: {e}")
        return 0.0


def _transcribe_with_qwen3(file_path: str, format_ext: str) -> Optional[str]:
    """Attempts transcription via OpenRouter Qwen3 ASR Flash."""
    keys_to_try: List[str] = []
    if settings.GPT_AUDIO_KEY:
        keys_to_try.append(settings.GPT_AUDIO_KEY.strip())
    if settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip() not in keys_to_try:
        keys_to_try.append(settings.OPENROUTER_API_KEY.strip())

    if not keys_to_try:
        return None

    try:
        with open(file_path, "rb") as f:
            encoded_audio = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read/encode audio file {file_path}: {e}")
        return None

    openrouter_format = format_ext.replace(".", "").lower()
    if openrouter_format == "m4a":
        openrouter_format = "mp4"

    model_name = "qwen/qwen3-asr-flash-2026-02-10"

    for key_str in keys_to_try:
        headers = {
            "Authorization": f"Bearer {key_str}",
            "HTTP-Referer": "https://lammah.site",
            "X-Title": "Lammah AI",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "input_audio": {
                "data": encoded_audio,
                "format": openrouter_format
            }
        }
        try:
            logger.info(f"🎙️ [ASR] Trying Qwen3 ASR Flash for {os.path.basename(file_path)}...")
            response = requests.post(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                headers=headers,
                json=payload,
                timeout=180
            )
            if response.status_code == 200:
                text = response.json().get("text", "").strip()
                logger.info(f"✅ [ASR] Qwen3 transcription succeeded ({len(text)} chars)")
                return text
            else:
                logger.warning(f"⚠️ [ASR] Qwen3 failed ({response.status_code}): {response.text[:200]}")
        except Exception as ex:
            logger.warning(f"⚠️ [ASR] Qwen3 request exception: {ex}")

    return None


def _transcribe_with_groq(file_path: str) -> Optional[str]:
    """Attempts transcription via Groq Whisper Large V3 (fast, high precision Arabic)."""
    groq_key = getattr(settings, "GROQ_API_KEY", None)
    if not groq_key or not groq_key.strip():
        return None

    try:
        from groq import Groq
        groq_client = Groq(api_key=groq_key.strip())
        logger.info(f"🎙️ [ASR] Fallback to Groq Whisper Large V3 for {os.path.basename(file_path)}...")
        
        with open(file_path, "rb") as f:
            res = groq_client.audio.transcriptions.create(
                file=(os.path.basename(file_path), f.read(), "audio/mpeg"),
                model="whisper-large-v3",
                timeout=120.0
            )
        text = (res.text or "").strip()
        logger.info(f"✅ [ASR] Groq Whisper transcription succeeded ({len(text)} chars)")
        return text
    except Exception as ex:
        logger.warning(f"⚠️ [ASR] Groq Whisper failed: {ex}")
        return None


def _transcribe_with_openrouter_whisper(file_path: str, model_name: str = "openai/whisper-large-v3") -> Optional[str]:
    """Attempts transcription via OpenRouter OpenAI Whisper."""
    keys_to_try: List[str] = []
    if settings.OPENROUTER_API_KEY:
        keys_to_try.append(settings.OPENROUTER_API_KEY.strip())
    if settings.GPT_AUDIO_KEY and settings.GPT_AUDIO_KEY.strip() not in keys_to_try:
        keys_to_try.append(settings.GPT_AUDIO_KEY.strip())

    if not keys_to_try:
        return None

    try:
        with open(file_path, "rb") as f:
            encoded_audio = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read/encode audio for Whisper: {e}")
        return None

    for key_str in keys_to_try:
        headers = {
            "Authorization": f"Bearer {key_str}",
            "HTTP-Referer": "https://lammah.site",
            "X-Title": "Lammah AI",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "input_audio": {
                "data": encoded_audio,
                "format": "mp3"
            }
        }
        try:
            logger.info(f"🎙️ [ASR] Trying OpenRouter {model_name} for {os.path.basename(file_path)}...")
            response = requests.post(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                headers=headers,
                json=payload,
                timeout=180
            )
            if response.status_code == 200:
                text = response.json().get("text", "").strip()
                logger.info(f"✅ [ASR] OpenRouter {model_name} succeeded ({len(text)} chars)")
                return text
            else:
                logger.warning(f"⚠️ [ASR] OpenRouter {model_name} failed ({response.status_code}): {response.text[:200]}")
        except Exception as ex:
            logger.warning(f"⚠️ [ASR] OpenRouter {model_name} error: {ex}")

    return None


def _transcribe_single_segment(file_path: str, format_ext: str = "mp3") -> str:
    """
    Transcribes a single audio file or chunk with multi-tier fallback:
    1. Qwen3 ASR Flash (OpenRouter)
    2. Groq Whisper Large V3
    3. OpenRouter Whisper Large V3
    4. OpenRouter Whisper-1
    """
    # Tier 1: Qwen3 ASR Flash
    result = _transcribe_with_qwen3(file_path, format_ext)
    if result is not None:
        return result

    # Tier 2: Groq Whisper Large V3
    result = _transcribe_with_groq(file_path)
    if result is not None:
        return result

    # Tier 3: OpenRouter Whisper Large V3
    result = _transcribe_with_openrouter_whisper(file_path, "openai/whisper-large-v3")
    if result is not None:
        return result

    # Tier 4: OpenRouter Whisper-1
    result = _transcribe_with_openrouter_whisper(file_path, "openai/whisper-1")
    if result is not None:
        return result

    raise RuntimeError("فشلت جميع محركات التفريغ الصوتي في معالجة الملف. يرجى التحقق من جودة الصوت أو المحاولة لاحقاً.")


def transcribe_audio(file_path: str, progress_callback=None) -> str:
    """
    Main transcription entry point:
    - Automatically checks duration and size.
    - If > 180 seconds (~3 min) or > 2.0 MB, chunks the file into 3-minute segments
      to stay within provider duration limits and generate timestamps.
    - Uses robust multi-tier fallback across Qwen3, Groq Whisper, and OpenRouter Whisper.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    # Auto-convert video to MP3 if passed directly
    video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
    if file_path.lower().endswith(video_exts):
        extracted_mp3 = f"{file_path}_converted.mp3"
        if not os.path.exists(extracted_mp3):
            if progress_callback:
                progress_callback("🎬 جاري استخراج الصوت النقي من ملف الفيديو عبر المحرك المسرّع...")
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", file_path,
                    "-vn", "-acodec", "libmp3lame", "-b:a", "64k", "-ac", "1",
                    extracted_mp3
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                file_path = extracted_mp3
            except Exception as vid_e:
                logger.error(f"Failed to extract audio from video {file_path}: {vid_e}")
        else:
            file_path = extracted_mp3

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    duration_sec = get_audio_duration_seconds(file_path)

    if progress_callback:
        progress_callback(f"📦 حجم الملف: {file_size_mb:.2f} ميجابايت (المدة: {int(duration_sec // 60)} دقيقة و {int(duration_sec % 60)} ثانية)")

    # If audio is longer than 180 seconds or larger than 2.0 MB, chunk into 3-minute segments
    if duration_sec > 180.0 or file_size_mb > 2.0:
        return _transcribe_large_audio(file_path, progress_callback)

    if progress_callback:
        progress_callback("⚡ إرسال الملف لمحرك لمّاح الذكي...")

    ext = os.path.splitext(file_path)[1].replace(".", "").lower() or "mp3"
    return _transcribe_single_segment(file_path, ext)


def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """Splits large audio into 3-minute chunks and concatenates the transcriptions with timestamps."""
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)

    segment_seconds = 180  # 3-minute segments ensure Qwen3 provider limit (<290s) is never hit

    if progress_callback:
        progress_callback("🎧 جاري معالجة وتفريغ الصوت بدقة فائقة...")

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1",  # 48kbps mono MP3 (~1MB per 3 min)
            f"{chunk_dir}/chunk_%03d.mp3"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ FFMPEG SPLIT FAILED: {e}")
        raise RuntimeError("فشل لمّاح في تجهيز وتجزئة الملف الصوتي. يرجى التأكد من أن الملف سليم وغير تالف.")

    chunks = sorted(glob.glob(f"{chunk_dir}/chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("لم يتم إنتاج أي مقاطع من عملية التقسيم.")

    import threading
    from concurrent.futures import ThreadPoolExecutor

    results: List[Optional[str]] = [None] * len(chunks)
    completed_count = 0
    lock = threading.Lock()

    def process_chunk(idx: int, chunk_path: str):
        nonlocal completed_count
        try:
            chunk_text = _transcribe_single_segment(chunk_path, "mp3")
            m_start = idx * 3
            m_end = (idx + 1) * 3
            timestamp_label = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n"
            results[idx] = timestamp_label + chunk_text
            with lock:
                completed_count += 1
                if progress_callback:
                    progress_callback(f"🎧 اكتمل تفريغ {completed_count} من {len(chunks)} مقاطع بسرعة قياسية...")
        except Exception as e:
            logger.error(f"❌ Chunk {idx+1} failed: {e}")
            raise RuntimeError(f"فشل تفريغ المقطع #{idx+1} بعد استنفاد كافة المحركات المتاحة: {e}")
        finally:
            try:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
            except Exception:
                pass

    # Concurrently transcribe chunks (up to 4 at a time)
    max_workers = min(4, len(chunks))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_chunk, idx, chunk_path) for idx, chunk_path in enumerate(chunks)]
        for future in futures:
            future.result()

    try:
        if os.path.exists(chunk_dir):
            os.rmdir(chunk_dir)
    except Exception:
        pass

    full_transcript = [r for r in results if r is not None]
    return " ".join(full_transcript).strip()
