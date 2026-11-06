import os
import base64
import glob
import subprocess
import time
import requests
from typing import Optional, List
from app.core.config import settings
from app.core.logger import logger

# Initialize Transcription Client placeholder to maintain compatibility
client = None
is_openrouter = True


def get_audio_duration_seconds(file_path: str) -> float:
    """Returns duration in seconds using ffprobe, or estimated from file size on failure."""
    if not file_path or not os.path.exists(file_path):
        return 0.0
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        val = float(out)
        if val > 0:
            return val
    except Exception as e:
        logger.warning(f"Could not read duration with ffprobe for {file_path}: {e}")

    # Fallback estimate based on file size (~16KB/s for 128kbps audio)
    try:
        size = os.path.getsize(file_path)
        return max(5.0, round(size / 16000.0, 1))
    except Exception:
        return 0.0


def _transcribe_with_groq(file_path: str) -> Optional[str]:
    """
    Tier 1: Groq Whisper Large V3.
    Ultra-fast and high precision for Arabic audio.
    """
    groq_key = getattr(settings, "GROQ_API_KEY", None)
    if not groq_key or not groq_key.strip():
        return None

    try:
        from groq import Groq
        groq_client = Groq(api_key=groq_key.strip())
        logger.info(f"🎙️ [ASR:Groq] Transcribing {os.path.basename(file_path)} via Whisper Large V3...")

        with open(file_path, "rb") as f:
            res = groq_client.audio.transcriptions.create(
                file=(os.path.basename(file_path), f.read(), "audio/mpeg"),
                model="whisper-large-v3",
                timeout=120.0
            )
        text = (res.text or "").strip()
        if text:
            logger.info(f"✅ [ASR:Groq] Transcription succeeded ({len(text)} chars)")
            return text
    except Exception as ex:
        logger.warning(f"⚠️ [ASR:Groq] Failed for {os.path.basename(file_path)}: {ex}")

    return None


def _transcribe_with_gemini(file_path: str, format_ext: str = "mp3") -> Optional[str]:
    """
    Tier 2: Google Gemini 2.5 Flash ASR.
    Massive context, state-of-the-art Arabic & academic vocabulary recognition,
    no 25MB Whisper limits or strict token throttling.
    """
    gemini_key = getattr(settings, "GEMINI_API_KEY", None)
    if not gemini_key or not gemini_key.strip():
        return None

    try:
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read/encode audio for Gemini: {e}")
        return None

    # Map file extension to standard MIME
    ext = format_ext.lower().replace(".", "")
    mime_map = {
        "mp3": "audio/mp3",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "aac": "audio/aac",
        "webm": "audio/webm",
        "weba": "audio/webm",
        "flac": "audio/flac",
    }
    mime_type = mime_map.get(ext, "audio/mp3")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key.strip()}"
    payload = {
        "contents": [{
            "parts": [
                {
                    "text": (
                        "Transcribe the spoken audio verbatim and accurately in its original language "
                        "(Arabic, English, or mixed medical/technical terms). "
                        "Output ONLY the exact verbatim transcription without any commentary, preamble, or metadata."
                    )
                },
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": audio_b64
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.0
        }
    }

    try:
        logger.info(f"🎙️ [ASR:Gemini] Transcribing {os.path.basename(file_path)} via Gemini 2.5 Flash...")
        response = requests.post(url, json=payload, timeout=90)
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    logger.info(f"✅ [ASR:Gemini] Transcription succeeded ({len(text)} chars)")
                    return text
        else:
            logger.warning(f"⚠️ [ASR:Gemini] Status {response.status_code}: {response.text[:200]}")
    except Exception as ex:
        logger.warning(f"⚠️ [ASR:Gemini] Exception for {os.path.basename(file_path)}: {ex}")

    return None


def _transcribe_with_openrouter_whisper(file_path: str, model_name: str = "openai/whisper-large-v3") -> Optional[str]:
    """
    Tier 3 / Tier 4: OpenRouter OpenAI Whisper.
    Reliable tertiary fallback when earlier tiers are unavailable.
    """
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
        logger.error(f"Failed to read/encode audio for OpenRouter Whisper: {e}")
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
            logger.info(f"🎙️ [ASR:OpenRouter] Trying {model_name} for {os.path.basename(file_path)}...")
            response = requests.post(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                headers=headers,
                json=payload,
                timeout=120
            )
            if response.status_code == 200:
                text = response.json().get("text", "").strip()
                if text:
                    logger.info(f"✅ [ASR:OpenRouter] {model_name} succeeded ({len(text)} chars)")
                    return text
            else:
                logger.warning(f"⚠️ [ASR:OpenRouter] {model_name} failed ({response.status_code}): {response.text[:200]}")
        except Exception as ex:
            logger.warning(f"⚠️ [ASR:OpenRouter] {model_name} error: {ex}")

    return None


def _transcribe_single_segment(file_path: str, format_ext: str = "mp3") -> str:
    """
    Transcribes a single audio file or chunk with indestructible multi-tier fallback:
    1. Groq Whisper Large V3 (fastest, high accuracy)
    2. Google Gemini 2.5 Flash ASR (heavyweight, robust against Whisper limits)
    3. OpenRouter Whisper Large V3
    4. OpenRouter Whisper-1
    """
    # Tier 1: Groq Whisper Large V3
    result = _transcribe_with_groq(file_path)
    if result:
        return result

    # Tier 2: Google Gemini 2.5 Flash
    result = _transcribe_with_gemini(file_path, format_ext)
    if result:
        return result

    # Tier 3: OpenRouter Whisper Large V3
    result = _transcribe_with_openrouter_whisper(file_path, "openai/whisper-large-v3")
    if result:
        return result

    # Tier 4: OpenRouter Whisper-1
    result = _transcribe_with_openrouter_whisper(file_path, "openai/whisper-1")
    if result:
        return result

    return ""


def transcribe_audio(file_path: str, progress_callback=None) -> str:
    """
    Main transcription entry point:
    - Automatically checks duration and size.
    - If > 180 seconds (~3 min) or > 2.0 MB, chunks the file into 3-minute segments
      to stay within provider duration limits and generate timestamps.
    - Uses robust multi-tier fallback across Groq, Gemini 2.5 Flash, and OpenRouter Whisper.
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
        progress_callback("⚡ إرسال الملف لمحرك لمّاح الذكي للتفريغ الفوري...")

    ext = os.path.splitext(file_path)[1].replace(".", "").lower() or "mp3"
    result = _transcribe_single_segment(file_path, ext)
    if result:
        return result

    # Last resort fallback attempt with Gemini
    gemini_fallback = _transcribe_with_gemini(file_path, ext)
    if gemini_fallback:
        return gemini_fallback

    raise RuntimeError("تعذر تفريغ المقطع الصوتي بعد استنفاد محركات التفريغ. يرجى التحقق من جودة الصوت أو المحاولة لاحقاً.")


def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """
    Splits large audio into 3-minute segments and concurrently transcribes them.
    Fault-tolerant: Single chunk hiccup or silent slice does NOT abort the whole lecture.
    """
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)

    segment_seconds = 180  # 3-minute segments

    if progress_callback:
        progress_callback("🎧 جاري تهيئة المقاطع الصوتية وهندستها للمعالجة المتوازية...")

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

            if chunk_text and chunk_text.strip():
                results[idx] = timestamp_label + chunk_text.strip()
            else:
                # Mark as silent/inaudible rather than crashing the whole batch
                logger.warning(f"⚠️ Chunk {idx+1}/{len(chunks)} produced empty text, marking as silent segment.")
                results[idx] = timestamp_label + "[مقطع صوتي غير مسموع أو هادئ]"

            with lock:
                completed_count += 1
                if progress_callback:
                    progress_callback(f"🎧 اكتمل تفريغ {completed_count} من {len(chunks)} مقاطع بدقة متناهية...")
        except Exception as e:
            logger.error(f"⚠️ Chunk {idx+1} encountered error: {e}. Gracefully recording inaudible slice.")
            m_start = idx * 3
            m_end = (idx + 1) * 3
            results[idx] = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n[مقطع صوتي غير واضح - تم استكمال التفريغ]"
            with lock:
                completed_count += 1
        finally:
            try:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
            except Exception:
                pass

    # Concurrently transcribe chunks (up to 3 at a time to prevent rate limits)
    max_workers = min(3, len(chunks))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_chunk, idx, chunk_path) for idx, chunk_path in enumerate(chunks)]
        for future in futures:
            future.result()

    try:
        if os.path.exists(chunk_dir):
            os.rmdir(chunk_dir)
    except Exception:
        pass

    full_chunks = [r for r in results if r is not None]
    audible_chunks = [r for r in full_chunks if "[مقطع صوتي غير" not in r]

    # If completely empty across all chunks, only then fail
    if not full_chunks or (len(audible_chunks) == 0 and len(full_chunks) > 0):
        raise RuntimeError("تعذر تفريغ المقطع الصوتي بعد استنفاد محركات التفريغ. يرجى التأكد من وضوح الصوت.")

    return "\n\n".join(full_chunks).strip()


def transcribe_precomputed_chunks(chunk_paths: List[str], progress_callback=None) -> str:
    """
    Transcribes a pre-generated list of 3-minute MP3 chunks produced by the Go media worker.
    Fault-tolerant: single chunk error does not abort entire lecture processing.
    """
    if not chunk_paths:
        return ""

    if len(chunk_paths) == 1:
        if progress_callback:
            progress_callback("⚡ إرسال الملف لمحرك التفريغ الصوتي فائق السرعة...")
        res = _transcribe_single_segment(chunk_paths[0], "mp3")
        if res:
            return res
        res = _transcribe_with_gemini(chunk_paths[0], "mp3")
        return res or ""

    import threading
    from concurrent.futures import ThreadPoolExecutor

    results: List[Optional[str]] = [None] * len(chunk_paths)
    completed_count = 0
    lock = threading.Lock()

    def process_chunk(idx: int, path: str):
        nonlocal completed_count
        try:
            chunk_text = _transcribe_single_segment(path, "mp3")
            m_start = idx * 3
            m_end = (idx + 1) * 3
            timestamp_label = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n"

            if chunk_text and chunk_text.strip():
                results[idx] = timestamp_label + chunk_text.strip()
            else:
                logger.warning(f"⚠️ Precomputed chunk {idx+1}/{len(chunk_paths)} produced empty text.")
                results[idx] = timestamp_label + "[مقطع صوتي غير مسموع أو هادئ]"

            with lock:
                completed_count += 1
                if progress_callback:
                    progress_callback(f"🎧 اكتمل تفريغ {completed_count} من {len(chunk_paths)} مقاطع في محرك التفريغ...")
        except Exception as e:
            logger.error(f"⚠️ Precomputed chunk {idx+1} error: {e}. Gracefully handling.")
            m_start = idx * 3
            m_end = (idx + 1) * 3
            results[idx] = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n[مقطع صوتي غير واضح - تم استكمال التفريغ]"
            with lock:
                completed_count += 1

    max_workers = min(3, len(chunk_paths))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_chunk, idx, cp) for idx, cp in enumerate(chunk_paths)]
        for future in futures:
            future.result()

    full_chunks = [r for r in results if r is not None]
    audible_chunks = [r for r in full_chunks if "[مقطع صوتي غير" not in r]

    if not full_chunks or (len(audible_chunks) == 0 and len(full_chunks) > 0):
        raise RuntimeError("تعذر تفريغ المقاطع الصوتية بعد استنفاد محركات التفريغ. يرجى التأكد من وضوح الصوت.")

    return "\n\n".join(full_chunks).strip()
