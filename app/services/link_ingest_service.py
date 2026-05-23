import os
import uuid
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
    if not _is_valid_http_url(lecture_url):
        raise ValueError("الرابط غير صالح. يرجى إدخال رابط صحيح يبدأ بـ http أو https.")

    lower_url = lecture_url.lower()
    if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
        raise ValueError("🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")
    if "zoom.us" in lower_url or "zoom.com" in lower_url:
        raise ValueError("🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")

    os.makedirs(upload_dir, exist_ok=True)
    parsed = urlparse(lecture_url.strip())
    target_ext = _safe_ext_from_path(parsed.path)
    fallback_name = f"{unique_id}_{uuid.uuid4().hex[:6]}{target_ext}"
    fallback_path = os.path.join(upload_dir, fallback_name)

    # We removed the direct HTTP download fallback because it downloads HTML pages for YouTube/Zoom links
    # and saves them as .mp3 files, which crashes the audio transcriber.
    # yt-dlp is perfectly capable of handling direct links anyway.

    try:
        import yt_dlp
    except Exception:
        raise RuntimeError(
            "تعذر معالجة الرابط تلقائيًا. الرجاء تثبيت مكتبة yt-dlp على الخادم، أو رفع الملف مباشرة."
        )

    output_template = os.path.join(upload_dir, f"{unique_id}_%(id)s.%(ext)s")
    ydl_opts = {
        # web_embedded client does NOT require PO tokens and works on datacenter IPs.
        # ios/mweb/android now require GVS PO tokens which we can't provide, so they are excluded.
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "nocheckcertificate": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["web_embedded", "web"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(lecture_url, download=True)
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                downloaded_path = requested[0]["filepath"]
            else:
                downloaded_path = ydl.prepare_filename(info)
            
            # Since FFmpegExtractAudio converts it to .mp3, ensure the path reflects this
            downloaded_path = os.path.splitext(downloaded_path)[0] + ".mp3"

        if not downloaded_path or not os.path.exists(downloaded_path):
            raise RuntimeError("فشل تنزيل الملف من الرابط.")

        logger.info(f"Downloaded lecture using yt-dlp: {downloaded_path}")
        return downloaded_path
    except Exception as e:
        err_str = str(e)
        logger.error(f"yt-dlp download failed: {err_str}")
        # Provide a more specific message when YouTube is blocking the server IP
        if "Sign in" in err_str or "bot" in err_str or "403" in err_str:
            raise RuntimeError(
                "❌ فشل جلب المحاضرة: يوتيوب يمنع التنزيل من السيرفر. "
                "يرجى تنزيل المحاضرة على جهازك ورفعها مباشرة كملف صوتي."
            )
        raise RuntimeError(
            "❌ فشل جلب المحاضرة من هذا الرابط. تأكد أن الرابط عام ومتاح بدون تسجيل دخول."
        )
