import os
from groq import Groq
from openai import OpenAI
from app.core.config import settings

import glob
import subprocess
from app.core.logger import logger

# Initialize Transcription Client
client = None
is_openrouter = False

if settings.WHISPER_API_KEY:
    if settings.WHISPER_API_KEY.startswith("sk-or-"):
        client = OpenAI(
            api_key=settings.WHISPER_API_KEY,
            base_url="https://openrouter.ai/api/v1"
        )
        is_openrouter = True
    else:
        client = Groq(api_key=settings.WHISPER_API_KEY)
elif settings.GROQ_API_KEY:
    client = Groq(api_key=settings.GROQ_API_KEY)

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
    
    if file_size_mb > 24.0: # Groq limit is 25MB
        return _transcribe_large_audio(file_path, progress_callback)
    
    if progress_callback:
        progress_callback("⚡ إرسال الملف لمحرك لمّاح الذكي...")
    
    model_name = "openai/whisper-large-v3" if is_openrouter else "whisper-large-v3"
    
    with open(file_path, "rb") as file:
        try:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), file.read(), "audio/mpeg"),
                model=model_name,
                timeout=300.0 # Increased timeout for reliability
            )
            return transcription.text
        except Exception as e:
            if is_openrouter and settings.GROQ_API_KEY:
                logger.warning(f"OpenRouter failed, falling back to Groq: {e}")
                groq_client = Groq(api_key=settings.GROQ_API_KEY)
                file.seek(0)
                transcription = groq_client.audio.transcriptions.create(
                    file=(os.path.basename(file_path), file.read(), "audio/mpeg"),
                    model="whisper-large-v3",
                    timeout=300.0
                )
                return transcription.text
            raise e

def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """Splits large audio into 10-minute chunks and concatenates the transcriptions."""
    assert client is not None, "Groq client must be initialized"
    
    # Use local state to avoid global mutation issues (UnboundLocalError)
    active_client = client
    active_is_openrouter = is_openrouter
    active_model = "openai/whisper-large-v3" if is_openrouter else "whisper-large-v3"
    
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    
    ext = os.path.splitext(file_path)[1]
    
    if progress_callback:
        progress_callback("✂️ الملف كبير، جاري تقسيمه لمقاطع (10 دقائق)...")
        
    # Split into 10-minute segments using ffmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-f", "segment", "-segment_time", "600",
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
            with open(chunk_path, "rb") as file:
                try:
                    logger.info(f"🎙️ لمّاح يحاول التفريغ (محاولة {attempt+1}/{retries})...")
                    transcription = active_client.audio.transcriptions.create(
                        file=(os.path.basename(chunk_path), file.read(), "audio/mpeg"),
                        model=active_model,
                        timeout=300.0
                    )
                    full_transcript.append(transcription.text)
                    success = True
                    break # Success
                except Exception as e:
                    err_msg = str(e)
                    logger.error(f"❌ CHUNK {idx} FAILED (Attempt {attempt+1}): {err_msg}")
                    
                    # If we get a JSON parsing error (likely a 502/504 from OpenRouter), wait and retry
                    if "No number after minus sign" in err_msg or "502" in err_msg or "504" in err_msg:
                        import time
                        time.sleep(5) # Wait longer between retries
                        continue
                    
                    if attempt < retries - 1:
                        import time
                        time.sleep(3)
                        continue
                    else:
                        # Fallback to Groq if OpenRouter fails 10 times
                        if active_is_openrouter and settings.GROQ_API_KEY:
                            logger.info("🚀 محرك لمّاح الأساسي متوقف مؤقتاً... ننتقل للمحرك البديل الفائق...")
                            try:
                                groq_client = Groq(api_key=settings.GROQ_API_KEY)
                                file.seek(0)
                                transcription = groq_client.audio.transcriptions.create(
                                    file=(os.path.basename(chunk_path), file.read(), "audio/mpeg"),
                                    model="whisper-large-v3",
                                    timeout=300.0
                                )
                                full_transcript.append(transcription.text)
                                # Update local state for remaining chunks
                                active_client = groq_client
                                active_is_openrouter = False
                                active_model = "whisper-large-v3"
                                success = True
                                break # Success with fallback
                            except Exception as fallback_e:
                                logger.error(f"❌ Lammah-Groq fallback failed: {str(fallback_e)}")
                                raise RuntimeError("⚠️ عذراً، محرك لمّاح يواجه ضغطاً استثنائياً حالياً. (REV-777)")
                        else:
                            raise RuntimeError(f"⚠️ عذراً، محرك لمّاح يواجه ضغطاً شديداً حالياً. (REV-777) الخطأ: {err_msg[:50]}")

        
        if not success:
            raise RuntimeError("فشل تفريغ أحد المقاطع بعد عدة محاولات.")
            
        # Cleanup chunk
        os.remove(chunk_path)
        
    # Cleanup directory
    try:
        os.rmdir(chunk_dir)
    except Exception:
        pass
        
    return " ".join(full_transcript)
