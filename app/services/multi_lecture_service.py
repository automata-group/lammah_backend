import os
import asyncio
import logging
from typing import List, Dict, Any
from app.services.audio_service import transcribe_audio
from app.services.link_ingest_service import download_audio_from_link
from app.core.config import settings

logger = logging.getLogger(__name__)

async def process_multiple_audio_sources(
    lecture_urls: List[str], 
    audio_temp_ids: List[str], 
    upload_dir: str, 
    temp_dir: str, 
    unique_session_id: str,
    progress_callback=None
) -> str:
    """
    Orchestrates downloading, preparing, and transcribing multiple audio sources (URLs and files).
    Returns a single concatenated transcript.
    """
    final_transcript_parts = []
    total_sources = len(lecture_urls) + len(audio_temp_ids)
    current_source = 0

    # 1. Process URLs via yt-dlp
    for idx, url in enumerate(lecture_urls):
        current_source += 1
        if progress_callback:
            progress_callback(f"🔗 جاري تجهيز المحاضرة (الرابط {idx+1}/{len(lecture_urls)})...")
        
        try:
            # Download audio from link
            logger.info(f"[MULTI-LECTURE] Downloading URL {idx+1}: {url}")
            audio_path = await asyncio.to_thread(
                download_audio_from_link, url.strip(), upload_dir, unique_session_id
            )
            
            # Transcribe the downloaded audio
            if progress_callback:
                progress_callback(f"🎧 جاري تفريغ المحاضرة من الرابط {idx+1}...")
            
            transcript = await asyncio.to_thread(transcribe_audio, audio_path)
            
            if transcript:
                final_transcript_parts.append(f"--- [محاضرة رابط {idx+1}] ---\n{transcript}\n")
        except Exception as e:
            logger.error(f"[MULTI-LECTURE] Failed to process URL {url}: {e}")
            if progress_callback:
                progress_callback(f"⚠️ فشل في معالجة الرابط {idx+1}، جاري التخطي...")

    # 2. Process Uploaded Files via Go Uploader Temp IDs
    for idx, temp_id in enumerate(audio_temp_ids):
        current_source += 1
        if progress_callback:
            progress_callback(f"📥 جاري معالجة الملف المرفوع ({idx+1}/{len(audio_temp_ids)})...")
            
        try:
            base, _ = os.path.splitext(temp_id)
            processed_p = os.path.join(temp_dir, f"{base}_processed.mp3")
            temp_path = os.path.join(temp_dir, temp_id)
            
            audio_path = None
            if os.path.exists(processed_p):
                audio_path = processed_p
            elif os.path.exists(temp_path):
                audio_path = temp_path
            else:
                logger.warning(f"[MULTI-LECTURE] File not found for temp_id: {temp_id}")
                continue

            if progress_callback:
                progress_callback(f"🎧 جاري تفريغ الملف المرفوع {idx+1}...")
                
            transcript = await asyncio.to_thread(transcribe_audio, audio_path)
            
            if transcript:
                final_transcript_parts.append(f"--- [ملف صوتي {idx+1}] ---\n{transcript}\n")
        except Exception as e:
            logger.error(f"[MULTI-LECTURE] Failed to process Temp ID {temp_id}: {e}")
            if progress_callback:
                progress_callback(f"⚠️ فشل في معالجة الملف المرفوع {idx+1}، جاري التخطي...")

    # 3. Combine everything into one giant transcript
    if not final_transcript_parts:
        raise ValueError("لم يتم تفريغ أي نص من الروابط أو الملفات المرفوعة.")
        
    combined_transcript = "\n\n".join(final_transcript_parts)
    logger.info(f"[MULTI-LECTURE] Successfully combined {len(final_transcript_parts)} transcripts into one.")
    
    return combined_transcript
