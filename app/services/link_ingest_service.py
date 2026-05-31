import os
from urllib.parse import urlparse

from app.core.logger import logger

def _is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _safe_ext_from_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".mp4", ".webm"}:
        return ext
    return ".mp3"


def _direct_download(url: str, output_path: str) -> bool:
    try:
        import requests

        with requests.get(url, stream=True, timeout=120) as response:
            if response.status_code != 200:
                return False
            with open(output_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.warning(f"Direct URL download failed: {e}")
        return False


def download_audio_from_link(lecture_url: str, upload_dir: str, unique_id: str) -> str:
    """Download audio from links. Routes YouTube URLs to the dedicated youtube_downloader_service."""
    if not _is_valid_http_url(lecture_url):
        raise ValueError("الرابط غير صالح. يرجى إدخال رابط صحيح يبدأ بـ http أو https.")

    lower_url = lecture_url.lower()
    
    # Route YouTube links to dedicated service
    if "youtube.com" in lower_url or "youtu.be" in lower_url:
        from app.services.youtube_downloader_service import download_youtube_audio
        return download_youtube_audio(lecture_url, upload_dir, unique_id)

    if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
        raise ValueError("🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")
    if "zoom.us" in lower_url or "zoom.com" in lower_url:
        raise ValueError("🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")

    os.makedirs(upload_dir, exist_ok=True)

    # Try direct download for direct audio files
    ext = _safe_ext_from_path(lecture_url)
    output_path = os.path.join(upload_dir, f"{unique_id}{ext}")
    if _direct_download(lecture_url, output_path):
        logger.info(f"Direct link download successful: {output_path}")
        return output_path

    raise ValueError("فشل تحميل الرابط المباشر. يرجى استخدام روابط يوتيوب أو رفع الملف الصوتي مباشرة.")
