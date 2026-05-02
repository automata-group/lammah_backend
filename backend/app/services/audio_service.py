import os
from groq import Groq
from openai import OpenAI
from app.core.config import settings

import glob
import subprocess
import logging

logger = logging.getLogger(__name__)

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
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(file_path), file.read()),
            model=model_name,
            timeout=120.0 # OpenRouter might be slower than Groq
        )
    return transcription.text

def _transcribe_large_audio(file_path: str, progress_callback=None) -> str:
    """Splits large audio into 10-minute chunks and concatenates the transcriptions."""
    assert client is not None, "Groq client must be initialized"
    
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
            "-c", "copy", f"{chunk_dir}/chunk_%03d{ext}"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("فشل تقسيم الملف الصوتي. يرجى التأكد من تثبيت ffmpeg.")
        
    chunks = sorted(glob.glob(f"{chunk_dir}/chunk_*{ext}"))
    if not chunks:
        raise RuntimeError("لم يتم إنتاج أي مقاطع من عملية التقسيم.")
        
    full_transcript = []
    
    for idx, chunk_path in enumerate(chunks):
        if progress_callback:
            progress_callback(f"🎧 جاري تفريغ المقطع {idx+1} من {len(chunks)}...")
            
        model_name = "openai/whisper-large-v3" if is_openrouter else "whisper-large-v3"
        
        with open(chunk_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(chunk_path), file.read()),
                model=model_name,
                timeout=120.0
            )
            full_transcript.append(transcription.text)
            
        # Cleanup chunk
        os.remove(chunk_path)
        
    # Cleanup directory
    try:
        os.rmdir(chunk_dir)
    except Exception:
        pass
        
    return " ".join(full_transcript)
