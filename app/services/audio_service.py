import os
from groq import Groq
from app.core.config import settings

import glob
import subprocess
import logging

logger = logging.getLogger(__name__)

client = Groq(api_key=settings.GROQ_API_KEY) if settings.GROQ_API_KEY else None

def transcribe_audio(file_path: str) -> str:
    """
    Transcribes audio file using Groq's high-speed Whisper-large-v3 API.
    Handles extra-large files by chunking them gracefully via ffmpeg if > 20MB.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    if not client:
        return "Groq API Key is missing. Cannot transcribe audio."
        
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    if file_size_mb > 20.0:
        logger.info(f"File size ({file_size_mb:.2f}MB) exceeds 20MB limit. Chunking audio...")
        return _transcribe_large_audio(file_path)
    
    with open(file_path, "rb") as file:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(file_path), file.read()),
            model="whisper-large-v3",
        )
    return transcription.text

def _transcribe_large_audio(file_path: str) -> str:
    """Splits large audio into 10-minute chunks and concatenates the transcriptions."""
    assert client is not None, "Groq client must be initialized"
    
    chunk_dir = f"{file_path}_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    
    ext = os.path.splitext(file_path)[1]
    
    # Split into 10-minute segments using ffmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-f", "segment", "-segment_time", "600",
            "-c", "copy", f"{chunk_dir}/chunk_%03d{ext}"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to chunk audio with ffmpeg: {e}")
        raise RuntimeError("فشل تقسيم الملف الصوتي. يرجى التأكد من تثبيت ffmpeg على الخادم، أو رفع ملفات أصغر من 20MB.")
        
    chunks = sorted(glob.glob(f"{chunk_dir}/chunk_*{ext}"))
    if not chunks:
        raise RuntimeError("لم يتم إنتاج أي مقاطع من عملية التقسيم.")
        
    full_transcript = []
    
    for idx, chunk_path in enumerate(chunks):
        logger.info(f"Transcribing chunk {idx+1}/{len(chunks)}...")
        with open(chunk_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(chunk_path), file.read()),
                model="whisper-large-v3",
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
