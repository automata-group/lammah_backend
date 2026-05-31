import os
import re
import time
import random
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


def _build_ydl_opts(
    upload_dir: str,
    unique_id: str,
    proxy: str | None = None,
    cookies_file: str | None = None,
    player_clients: list[str] | None = None,
) -> dict:
    """Build yt-dlp options dict consistent with ytdl-service."""
    output_template = os.path.join(upload_dir, f"{unique_id}_%(id)s.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "nocheckcertificate": True,
        "extract_flat": False,
        "force_keyframes_at_cuts": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }
    if proxy:
        opts["proxy"] = proxy
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    if player_clients:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": player_clients,
                "skip": ["webpage", "configs"],
            }
        }
    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    if po_token:
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "")
        opener = opts.setdefault("extractor_args", {})
        opener.setdefault("youtube", {})["po_token"] = (
            f"po_token_web={po_token}" + (f"+visitor_data={visitor_data}" if visitor_data else "")
        )
    return opts


def _safe_ydl_download(ydl_opts: dict, url: str) -> str:
    """Run yt-dlp and return the final mp3 path."""
    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get("requested_downloads") or []
        if requested and requested[0].get("filepath"):
            path = requested[0]["filepath"]
        else:
            path = ydl.prepare_filename(info)
    path = os.path.splitext(path)[0] + ".mp3"
    if not path or not os.path.exists(path):
        raise RuntimeError("فشل تنزيل الملف من الرابط.")
    return path


def _try_invidious_fallback(url: str, upload_dir: str, unique_id: str) -> str | None:
    """Fallback via public Invidious instances."""
    import requests
    instances = os.getenv(
        "INVIDIOUS_INSTANCES",
        "https://inv.nadeko.net,https://yewtu.be,https://invidious.snopyta.org,https://inv.vern.cc"
    ).split(",")
    random.shuffle(instances)
    video_id_match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    if not video_id_match:
        return None
    video_id = video_id_match.group(1)

    for instance in instances:
        instance = instance.strip()
        if not instance:
            continue
        try:
            api_url = f"{instance.rstrip('/')}/api/v1/videos/{video_id}"
            resp = requests.get(api_url, timeout=10)
            if resp.status_code != 200:
                continue
            streams = resp.json().get("adaptiveFormats", [])
            audio = [s for s in streams if s.get("type", "").startswith("audio")]
            if not audio:
                continue
            best = max(audio, key=lambda s: s.get("bitrate", 0))
            audio_url = best.get("url") or best.get("url")
            if not audio_url:
                continue
            fp = os.path.join(upload_dir, f"{unique_id}_invidious.mp3")
            with requests.get(audio_url, stream=True, timeout=300) as r:
                if r.status_code != 200:
                    continue
                with open(fp, "wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(fp) and os.path.getsize(fp) > 0:
                logger.info(f"Downloaded via Invidious {instance}: {fp}")
                return fp
        except Exception as e:
            logger.warning(f"Invidious {instance} failed: {e}")
    return None


def download_audio_from_link(lecture_url: str, upload_dir: str, unique_id: str) -> str:
    if not _is_valid_http_url(lecture_url):
        raise ValueError("الرابط غير صالح. يرجى إدخال رابط صحيح يبدأ بـ http أو https.")

    lower_url = lecture_url.lower()

    if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
        raise ValueError("🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")
    if "zoom.us" in lower_url or "zoom.com" in lower_url:
        raise ValueError("🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥")

    os.makedirs(upload_dir, exist_ok=True)

    # ===== 1. Try dedicated ytdl-service container =====
    from app.core.config import settings
    if settings.YOUTUBE_DOWNLOADER_URL:
        import requests
        try:
            logger.info(f"Using ytdl-service at {settings.YOUTUBE_DOWNLOADER_URL}")
            resp = requests.post(
                f"{settings.YOUTUBE_DOWNLOADER_URL.rstrip('/')}/download",
                json={"url": lecture_url, "unique_id": unique_id, "upload_dir": upload_dir},
                timeout=300
            )
            if resp.status_code == 200:
                data = resp.json()
                fp = data.get("filepath")
                fn = data.get("filename")
                backend = os.path.join(upload_dir, fn)
                if os.path.exists(backend):
                    return backend
                if fp and os.path.exists(fp):
                    return fp
                logger.error(f"ytdl-service success but file missing: backend={backend}, service={fp}")
            else:
                detail = ""
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                logger.error(f"ytdl-service status {resp.status_code}: {detail}")
                if "403" in detail or "Sign in" in detail or "bot" in detail:
                    raise RuntimeError(
                        "❌ يوتيوب يمنع التنزيل من السيرفر حتى مع الوكيل. "
                        "يرجى رفع الملف مباشرة."
                    )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"ytdl-service error: {e}. Falling back to local yt-dlp.")

    # ===== 2. Local yt-dlp with multi-strategy =====
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp غير مثبت على الخادم. يرجى تثبيته أو رفع الملف مباشرة."
        )

    import random
    proxy = os.getenv("YOUTUBE_PROXY")
    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", "/app/youtube_cookies.txt")
    cookies_file = cookies_path if os.path.exists(cookies_path) else None

    strategies = [
        {"proxy": proxy, "cookies": cookies_file, "clients": ["android", "web_embedded"]},
        {"proxy": proxy, "cookies": None,         "clients": ["android", "web_embedded"]},
        {"proxy": None,  "cookies": cookies_file, "clients": ["web_embedded", "web"]},
        {"proxy": None,  "cookies": None,         "clients": ["web_embedded"]},
    ]

    last_error = ""
    for s in strategies:
        try:
            opts = _build_ydl_opts(upload_dir, unique_id, s["proxy"], s["cookies"], s["clients"])
            result = _safe_ydl_download(opts, lecture_url)
            logger.info(f"Downloaded via yt-dlp: {result}")
            return result
        except Exception as e:
            last_error = str(e)
            logger.warning(f"yt-dlp strategy proxy={bool(s['proxy'])} cookies={bool(s['cookies'])} failed: {last_error[:200]}")
            time.sleep(1)

    # ===== 3. Invidious fallback =====
    logger.info("Trying Invidious fallback...")
    invidious_path = _try_invidious_fallback(lecture_url, upload_dir, unique_id)
    if invidious_path:
        return invidious_path

    logger.error(f"❌ YouTube/Link download failed for URL {lecture_url}. Last error: {last_error}")
    err_lower = last_error.lower()
    if "sign in" in err_lower or "bot" in err_lower or "403" in err_lower:
        raise RuntimeError(
            "❌ فشل جلب المحاضرة: يوتيوب يمنع التنزيل من السيرفر بشكل نهائي.\n"
            "الحلول:\n"
            "1. ضع ملف cookies.txt في المسار YOUTUBE_COOKIES_PATH\n"
            "2. استخدم وكيل سكني (residential proxy)\n"
            "3. نزّل المحاضرة على جهازك وارفعها كملف صوتي مباشرة."
        )
    raise RuntimeError(
        f"❌ فشل جلب المحاضرة. آخر خطأ: {last_error[:300]}"
    )
