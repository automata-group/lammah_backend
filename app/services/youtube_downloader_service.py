import os
import re
import time
import random
from typing import Any, Optional, Dict, List
from urllib.parse import urlparse

from app.core.logger import logger

def _build_ydl_opts(
    upload_dir: str,
    unique_id: str,
    proxy: str | None = None,
    cookies_file: str | None = None,
    player_clients: list[str] | None = None,
) -> dict:
    """Build yt-dlp options dict consistent with ytdl-service."""
    output_template = os.path.join(upload_dir, f"{unique_id}_%(id)s.%(ext)s")
    opts: dict[str, Any] = {
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
        "remote_components": ["ejs:github"],
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


def download_youtube_audio(youtube_url: str, upload_dir: str, unique_id: str) -> str:
    """Download audio from YouTube using multiple strategy fallbacks."""
    os.makedirs(upload_dir, exist_ok=True)

    # ===== 1. Try dedicated ytdl-service container =====
    from app.core.config import settings
    if settings.YOUTUBE_DOWNLOADER_URL:
        import requests
        try:
            logger.info(f"Using ytdl-service at {settings.YOUTUBE_DOWNLOADER_URL}")
            resp = requests.post(
                f"{settings.YOUTUBE_DOWNLOADER_URL.rstrip('/')}/download",
                json={"url": youtube_url, "unique_id": unique_id, "upload_dir": upload_dir},
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

    proxy = os.getenv("YOUTUBE_PROXY")
    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", "/app/youtube_cookies.txt")
    cookies_file = cookies_path if os.path.exists(cookies_path) else None

    strategies: list[dict[str, Any]] = [
        # Best: WARP proxy with auto client selection (proven to work)
        {"name": "proxy only (auto client)",          "proxy": proxy, "cookies": None,         "clients": None},
        {"name": "proxy + cookies (auto client)",      "proxy": proxy, "cookies": cookies_file, "clients": None},
        # Direct fallbacks
        {"name": "direct (auto client)",               "proxy": None,  "cookies": None,         "clients": None},
        {"name": "direct + cookies (auto client)",     "proxy": None,  "cookies": cookies_file, "clients": None},
        # Specific clients
        {"name": "proxy + ios client",                 "proxy": proxy, "cookies": None,         "clients": ["ios"]},
        {"name": "proxy + mweb client",                "proxy": proxy, "cookies": None,         "clients": ["mweb"]},
        {"name": "direct + ios client",                "proxy": None,  "cookies": None,         "clients": ["ios"]},
        {"name": "direct + mweb client",               "proxy": None,  "cookies": None,         "clients": ["mweb"]},
    ]

    last_error = ""
    for s in strategies:
        try:
            logger.info(f"[Local yt-dlp Strategy] {s['name']}")
            opts = _build_ydl_opts(
                upload_dir=upload_dir,
                unique_id=unique_id,
                proxy=s["proxy"],
                cookies_file=s["cookies"],
                player_clients=s["clients"],
            )
            result = _safe_ydl_download(opts, youtube_url)
            logger.info(f"Downloaded via yt-dlp strategy '{s['name']}': {result}")
            return result
        except Exception as e:
            last_error = str(e)
            logger.warning(f"yt-dlp strategy '{s['name']}' failed: {last_error[:200]}")
            time.sleep(1)

    # ===== 3. Invidious fallback =====
    logger.info("Trying Invidious fallback...")
    invidious_path = _try_invidious_fallback(youtube_url, upload_dir, unique_id)
    if invidious_path:
        return invidious_path

    # Log the fatal error in details
    logger.error(f"❌ YouTube download failed for URL {youtube_url}. Last error: {last_error}")

    err_lower = last_error.lower()
    if "sign in" in err_lower or "bot" in err_lower or "403" in err_lower:
        raise RuntimeError(
            "❌ فشل جلب المحاضرة: يوتيوب يمنع التنزيل من السيرفر بشكل نهائي.\n"
            "الحلول:\n"
            "1. وضع ملف cookies.txt في المسار YOUTUBE_COOKIES_PATH\n"
            "2. استخدم وكيل سكني (residential proxy)\n"
            "3. نزّل المحاضرة على جهازك وارفعها كملف صوتي مباشرة."
        )
    raise RuntimeError(
        f"❌ فشل جلب المحاضرة. آخر خطأ: {last_error[:300]}"
    )

# Force rebuild trigger 1
