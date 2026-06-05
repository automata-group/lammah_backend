import os
import base64
import glob
import subprocess
import requests
from app.core.config import settings
from app.core.logger import logger

# Initialize Transcription Client placeholder to maintain compatibility
client = None
is_openrouter = True

def transcribe_audio(file_path: str, progress_callback=None) -> str:
    """
    Transcribes audio file using strictly Qwen3 ASR Flash API via OpenRouter.
    Handles extra-large files by chunking them gracefully via ffmpeg if > 2MB.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
        
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if progress_callback:
        progress_callback(f"📦 حجم الملف: {file_size_mb:.2f} ميجابايت")
    
    if file_size_mb > 2.0:  # Auto-chunk files > 2MB for beautiful 5-minute timestamps!
        return _transcribe_large_audio(file_path, progress_callback)
    
    if progress_callback:
        progress_callback("⚡ إرسال الملف لمحرك لمّاح الذكي...")
    
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
            
        # Encode file to base64 (raw string, no prefix)
        with open(file_path, "rb") as f:
            encoded_audio = base64.b64encode(f.read()).decode('utf-8')
        
        ext = os.path.splitext(file_path)[1].replace(".", "").lower()
        if ext == "m4a":
            ext = "mp4"  # OpenRouter mapping
            
        model_name = "qwen/qwen3-asr-flash-2026-02-10"
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
            try:
                logger.info(f"🌐 [OR-FIX] Trying model: {model_name} with format: {ext}")
                payload = {
                    "model": model_name,
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
                    last_error = f"Model {model_name} failed ({response.status_code}): {response.text}"
                    logger.warning(last_error)
                    continue 
            except Exception as inner_e:
                last_error = str(inner_e)
                logger.error(f"❌ Error with model {model_name}: {last_error}")
                continue

        if success:
            return transcription_text
        
        raise RuntimeError(f"OpenRouter failed Qwen3. Last error: {last_error}")
    except Exception as e:
        logger.error(f"❌ OpenRouter process error: {e}")
        raise e


def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """Splits large audio into 5-minute chunks and concatenates the transcriptions."""
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    
    if progress_callback:
        progress_callback("✂️ جاري تقسيم الملف لمقاطع (5 دقائق) لتوليد التوقيتات...")
        
    # Split into 5-minute segments using ffmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-f", "segment", "-segment_time", "300",
            "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1",  # Re-encode to small mono MP3 for high reliability
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
                
                model_name = "qwen/qwen3-asr-flash-2026-02-10"
                chunk_success = False
                
                for key_str in keys_to_try:
                    headers = {
                        "Authorization": f"Bearer {key_str}",
                        "HTTP-Referer": "https://lammah.site",
                        "X-Title": "Lammah AI",
                        "Content-Type": "application/json"
                    }
                    try:
                        logger.info(f"🎙️ [LAMMAH] Transcribing chunk with model: {model_name}")
                        payload = {
                            "model": model_name,
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
                            break
                        else:
                            logger.warning(f"Chunk fail with {model_name} ({response.status_code}): {response.text}")
                    except Exception as chunk_e:
                        logger.error(f"Chunk error with {model_name}: {chunk_e}")
                        continue
                
                if chunk_success:
                    break
                else:
                    raise RuntimeError(f"Qwen3 failed for chunk {idx}")
            except Exception as e:
                err_msg = str(e)
                logger.error(f"❌ CHUNK {idx} FAILED (Attempt {attempt+1}): {err_msg}")
                
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
