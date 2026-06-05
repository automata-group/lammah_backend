import os
from groq import Groq
from openai import OpenAI
from app.core.config import settings
import base64

import glob
import subprocess
from app.core.logger import logger

# Initialize Transcription Client
client = None
is_openrouter = False

if settings.GPT_AUDIO_KEY:
    key = settings.GPT_AUDIO_KEY.strip()
    if key.startswith("sk-or-"):
        logger.info(f"[INIT] Using OpenRouter for Lammah Audio Engine (Key: {key[:10]}...)")
        client = OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1"
        )
        is_openrouter = True
    else:
        logger.info(f"[INIT] Using Direct API for Audio (Key: {key[:10]}...)")
        client = Groq(api_key=key)
elif settings.GROQ_API_KEY:
    logger.info(f"[INIT] Using Groq API Key for Whisper")
    client = Groq(api_key=settings.GROQ_API_KEY.strip())
else:
    logger.error("[INIT] No API Key found for transcription!")

def transcribe_audio(file_path: str, progress_callback=None) -> str:
    """
    Transcribes audio file using Groq's high-speed Whisper-large-v3 API.
    Handles extra-large files by chunking them gracefully via ffmpeg if > 25MB.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    if not client:
        return "Groq API Key is missing. Cannot transcribe audio."
        
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if progress_callback:
        progress_callback(f"📦 حجم الملف: {file_size_mb:.2f} ميجابايت")
    
    if file_size_mb > 2.0: # Auto-chunk files > 2MB for beautiful 5-minute timestamps!
        return _transcribe_large_audio(file_path, progress_callback)
    
    if progress_callback:
        progress_callback("⚡ إرسال الملف لمحرك لمّاح الذكي...")
    
    model_name = "openai/whisper-large-v3" if is_openrouter else "whisper-large-v3"
    
    if is_openrouter:
        import requests
        try:
            if progress_callback:
                progress_callback("⚡ جاري الإرسال لمحرك لمّاح الصوتي الذكي...")
            
            # Get all available OpenRouter keys to try
            keys_to_try = []
            if settings.GPT_AUDIO_KEY:
                keys_to_try.append(settings.GPT_AUDIO_KEY.strip())
            if settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip() not in keys_to_try:
                keys_to_try.append(settings.OPENROUTER_API_KEY.strip())
            
            if not keys_to_try:
                raise RuntimeError("No OpenRouter keys configured.")
                
            # Helper to encode file to base64 (raw string, no prefix)
            with open(file_path, "rb") as f:
                encoded_audio = base64.b64encode(f.read()).decode('utf-8')
            
            ext = os.path.splitext(file_path)[1].replace(".", "").lower()
            if ext == "m4a": ext = "mp4" # OpenRouter mapping
            
            # Strictly use Qwen ASR Flash as primary, with GPT Audio Mini and Whisper-1 as fallback
            models_to_try = ["qwen/qwen3-asr-flash-2026-02-10", "openai/gpt-4o-mini-transcribe", "openai/whisper-1"]
            last_error = ""
            success = False
            transcription_text = ""
            
            for key_str in keys_to_try:
                headers = {
                    "Authorization": f"Bearer {key_str}",
                    "HTTP-Referer": "https://lammah.site",
                    "X-Title": "Lammah AI",
                    "Content-Type": "application/json"
                }
                for m in models_to_try:
                    try:
                        logger.info(f"🌐 [OR-FIX] Trying model: {m} with format: {ext}")
                        payload = {
                            "model": m,
                            "input_audio": {
                                "data": encoded_audio,
                                "format": ext
                            }
                        }
                        
                        response = requests.post(
                            "https://openrouter.ai/api/v1/audio/transcriptions",
                            headers=headers,
                            json=payload,
                            timeout=300
                        )
                        
                        if response.status_code == 200:
                            transcription_text = response.json().get("text", "")
                            success = True
                            break
                        else:
                            last_error = f"Model {m} failed ({response.status_code}): {response.text}"
                            logger.warning(last_error)
                            continue 
                    except Exception as inner_e:
                        last_error = str(inner_e)
                        logger.error(f"❌ Error with model {m}: {last_error}")
                        continue
                if success:
                    break

            if success:
                return transcription_text

            # If both OR models fail, try Groq as last resort
            if settings.GROQ_API_KEY:
                logger.info("🔄 [OR] Both OR models failed. Falling back to Groq...")
                groq_client = Groq(api_key=settings.GROQ_API_KEY.strip())
                with open(file_path, "rb") as f:
                    transcription = groq_client.audio.transcriptions.create(
                        file=(os.path.basename(file_path), f.read(), "audio/mpeg"),
                        model="whisper-large-v3",
                        timeout=300.0
                    )
                    return transcription.text
            
            raise RuntimeError(f"OpenRouter failed all models. Last error: {last_error}")
        except Exception as e:
            logger.error(f"❌ OpenRouter process error: {e}")
            if settings.GROQ_API_KEY:
                # Last resort fallback
                groq_client = Groq(api_key=settings.GROQ_API_KEY.strip())
                with open(file_path, "rb") as f:
                    transcription = groq_client.audio.transcriptions.create(
                        file=(os.path.basename(file_path), f.read(), "audio/mpeg"),
                        model="whisper-large-v3",
                        timeout=300.0
                    )
                    return transcription.text
            raise e
    else:
        # Standard Groq logic
        with open(file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), file.read(), "audio/mpeg"),
                model=model_name,
                timeout=300.0
            )
            return transcription.text


def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """Splits large audio into 10-minute chunks and concatenates the transcriptions."""
    assert client is not None, "Groq client must be initialized"
    
    # Use local state to avoid global mutation issues (UnboundLocalError)
    active_client = client
    active_is_openrouter = is_openrouter
    active_model = "qwen/qwen3-asr-flash-2026-02-10" if is_openrouter else "whisper-large-v3"
    
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    
    ext = os.path.splitext(file_path)[1]
    
    if progress_callback:
        progress_callback("✂️ جاري تقسيم الملف لمقاطع (5 دقائق) لتوليد التوقيتات...")
        
    # Split into 5-minute segments using ffmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-f", "segment", "-segment_time", "300",
            "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", # Re-encode to small mono MP3 for high reliability
            f"{chunk_dir}/chunk_%03d.mp3"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ FFMPEG SPLIT FAILED: {e}")
        raise RuntimeError("فشل لمّاح في تجهيز الملف الصوتي. يرجى التأكد من أن الملف سليم وغير تالف.")
        
    chunks = sorted(glob.glob(f"{chunk_dir}/chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("لم يتم إنتاج أي مقاطع من عملية التقسيم.")
        
    full_transcript = []
    
    for idx, chunk_path in enumerate(chunks):
        if progress_callback:
            progress_callback(f"🎧 جاري تفريغ المقطع {idx+1} من {len(chunks)}...")
            
        retries = 10
        success = False
        for attempt in range(retries):
            try:
                logger.info(f"🎙️ لمّاح يحاول التفريغ (محاولة {attempt+1}/{retries})...")
                
                if active_is_openrouter:
                    import requests
                    
                    # Get all available OpenRouter keys to try
                    keys_to_try = []
                    if settings.GPT_AUDIO_KEY:
                        keys_to_try.append(settings.GPT_AUDIO_KEY.strip())
                    if settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip() not in keys_to_try:
                        keys_to_try.append(settings.OPENROUTER_API_KEY.strip())
                    
                    if not keys_to_try:
                        raise RuntimeError("No OpenRouter keys configured.")
                        
                    # Encode chunk to base64
                    with open(chunk_path, "rb") as f:
                        encoded_chunk = base64.b64encode(f.read()).decode('utf-8')
                    
                    # Strictly use Qwen ASR Flash with GPT and Whisper-1 as fallback
                    models_to_try = ["qwen/qwen3-asr-flash-2026-02-10", "openai/gpt-4o-mini-transcribe", "openai/whisper-1"]
                    
                    chunk_success = False
                    for key_str in keys_to_try:
                        headers = {
                            "Authorization": f"Bearer {key_str}",
                            "HTTP-Referer": "https://lammah.site",
                            "X-Title": "Lammah AI",
                            "Content-Type": "application/json"
                        }
                        for m in models_to_try:
                            try:
                                logger.info(f"🎙️ [LAMMAH] Transcribing chunk with model: {m}")
                                payload = {
                                    "model": m,
                                    "input_audio": {
                                        "data": encoded_chunk,
                                        "format": "mp3"
                                    }
                                }
                                
                                response = requests.post(
                                    "https://openrouter.ai/api/v1/audio/transcriptions",
                                    headers=headers,
                                    json=payload,
                                    timeout=300
                                )
                                
                                if response.status_code == 200:
                                    chunk_text = response.json().get("text", "").strip()
                                    m_start = idx * 5
                                    m_end = (idx + 1) * 5
                                    timestamp_label = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n"
                                    full_transcript.append(timestamp_label + chunk_text)
                                    success = True
                                    chunk_success = True
                                    # If this model worked, stick with it for next chunks
                                    active_model = m
                                    break
                                else:
                                    logger.warning(f"Chunk fail with {m} ({response.status_code}): {response.text}")
                            except Exception as chunk_e:
                                logger.error(f"Chunk error with {m}: {chunk_e}")
                                continue
                        if chunk_success:
                            break
                    
                    if chunk_success:
                        break
                    else:
                        raise RuntimeError(f"Both OR models failed for chunk {idx}")
                else:
                    # Groq logic
                    with open(chunk_path, "rb") as file:
                        transcription = active_client.audio.transcriptions.create(
                            file=(os.path.basename(chunk_path), file.read(), "audio/mpeg"),
                            model=active_model,
                            timeout=300.0
                        )
                        chunk_text = transcription.text.strip()
                        m_start = idx * 5
                        m_end = (idx + 1) * 5
                        timestamp_label = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n"
                        full_transcript.append(timestamp_label + chunk_text)
                        success = True
                        break
            except Exception as e:
                err_msg = str(e)
                logger.error(f"❌ CHUNK {idx} FAILED (Attempt {attempt+1}): {err_msg}")
                
                # If OpenRouter fails, immediately switch to Groq if key is available, without waiting for 10 retries!
                if active_is_openrouter and settings.GROQ_API_KEY:
                    logger.info("🚀 [OR-FAIL-IMMEDIATE] Switch to Groq immediately due to OpenRouter limitation...")
                    try:
                        groq_client = Groq(api_key=settings.GROQ_API_KEY.strip())
                        with open(chunk_path, "rb") as f:
                            transcription = groq_client.audio.transcriptions.create(
                                file=(os.path.basename(chunk_path), f.read(), "audio/mpeg"),
                                model="whisper-large-v3",
                                timeout=300.0
                            )
                            chunk_text = transcription.text.strip()
                            m_start = idx * 5
                            m_end = (idx + 1) * 5
                            timestamp_label = f"\n\n⏱️ [{m_start:02d}:00 - {m_end:02d}:00]\n"
                            full_transcript.append(timestamp_label + chunk_text)
                            
                            # Make the switch PERMANENT for all subsequent chunks!
                            active_client = groq_client
                            active_is_openrouter = False
                            active_model = "whisper-large-v3"
                            
                            success = True
                            break
                    except Exception as fallback_e:
                        logger.error(f"❌ Lammah-Groq fallback failed: {str(fallback_e)}")
                
                if not success:
                    if attempt < retries - 1:
                        import time
                        time.sleep(5)
                        continue
                    else:
                        raise RuntimeError(f"⚠️ عذراً، محرك لمّاح يواجه ضغطاً شديداً حالياً. (REV-777)")


        
        if not success:
            raise RuntimeError("فشل تفريغ أحد المقاطع بعد عدة محاولات مكثفة. يرجى المحاولة لاحقاً.")
            
        # Cleanup chunk
        os.remove(chunk_path)
        
    # Cleanup directory
    try:
        os.rmdir(chunk_dir)
    except Exception:
        pass
        
    return " ".join(full_transcript)
