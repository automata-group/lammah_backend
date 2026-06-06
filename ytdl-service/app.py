import os
import re
import json
import time
import random
from urllib.parse import urlparse
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yt_dlp
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ytdl-service")

COOKIES_PATH = os.getenv("YOUTUBE_COOKIES_PATH", "/app/youtube_cookies.txt")
INVIDIOUS_INSTANCES = os.getenv(
    "INVIDIOUS_INSTANCES",
    "https://inv.nadeko.net,https://yewtu.be,https://invidious.snopyta.org,https://inv.vern.cc"
).split(",")

app = FastAPI(title="Lammah YouTube Downloader Service")


class DownloadRequest(BaseModel):
    url: str
    unique_id: str
    upload_dir: str = "/app/uploads"


def safe_download(ydl_opts: dict, url: str) -> str:
    """Run yt-dlp and return the downloaded file path."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get("requested_downloads") or []
        if requested and requested[0].get("filepath"):
            downloaded_path = requested[0]["filepath"]
        else:
            downloaded_path = ydl.prepare_filename(info)
        downloaded_path = os.path.splitext(downloaded_path)[0] + ".mp3"

    if not downloaded_path or not os.path.exists(downloaded_path):
        raise RuntimeError("Downloaded file not found on disk after yt-dlp execution.")
    return downloaded_path


def build_ydl_opts(
    upload_dir: str,
    unique_id: str,
    proxy: Optional[str] = None,
    cookies_file: Optional[str] = None,
    player_clients: Optional[list[str]] = None,
    extractor_args: Optional[dict] = None,
) -> dict:
    """Build yt-dlp options dict with all anti-block measures."""
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

    if extractor_args:
        existing = opts.get("extractor_args", {})
        existing["youtube"] = {**existing.get("youtube", {}), **extractor_args}
        opts["extractor_args"] = existing

    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    if po_token:
        logger.info("Using PO_TOKEN for YouTube extraction")
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "")
        opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = (
            f"po_token_web={po_token}" + (f"+visitor_data={visitor_data}" if visitor_data else "")
        )

    return opts


def try_with_invidious(url: str, upload_dir: str, unique_id: str) -> Optional[str]:
    """Fallback: download via public Invidious instances."""
    import requests

    random.shuffle(INVIDIOUS_INSTANCES)
    for instance in INVIDIOUS_INSTANCES:
        instance = instance.strip()
        if not instance:
            continue
        try:
            logger.info(f"Trying Invidious instance: {instance}")
            api_url = f"{instance.rstrip('/')}/api/v1/videos/{url.split('=')[-1].split('&')[0]}"
            resp = requests.get(api_url, timeout=10)
            if resp.status_code != 200:
                continue

            video_data = resp.json()
            audio_streams = [
                s for s in video_data.get("adaptiveFormats", [])
                if s.get("type", "").startswith("audio")
            ]
            if not audio_streams:
                continue

            best_audio = max(audio_streams, key=lambda s: s.get("bitrate", 0))
            audio_url = best_audio.get("url") or best_audio.get("url")
            if not audio_url:
                continue

            filename = f"{unique_id}_invidious.mp3"
            filepath = os.path.join(upload_dir, filename)

            with requests.get(audio_url, stream=True, timeout=300) as r:
                if r.status_code != 200:
                    continue
                with open(filepath, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                logger.info(f"Downloaded via Invidious instance {instance}: {filepath}")
                return filepath
        except Exception as e:
            logger.warning(f"Invidious instance {instance} failed: {e}")
            continue
    return None


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/download")
def download_youtube_video(req: DownloadRequest):
    url = req.url
    unique_id = req.unique_id
    upload_dir = req.upload_dir

    logger.info(f"Received download request: url={url}, unique_id={unique_id}")

    lower_url = url.strip().lower()
    if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
        raise HTTPException(status_code=400, detail="Google Drive links are not supported.")
    if "zoom.us" in lower_url or "zoom.com" in lower_url:
        raise HTTPException(status_code=400, detail="Zoom links are not supported.")

    try:
        os.makedirs(upload_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create upload directory: {str(e)}")

    # ===== Strategy 1: Direct with proxy + cookies + PO token =====
    proxy = os.getenv("YOUTUBE_PROXY")
    cookies_file = COOKIES_PATH if os.path.exists(COOKIES_PATH) else None

    strategies = [
        # ===== Best strategies first: WARP proxy without forcing a specific client =====
        # (default yt-dlp client selection works best with WARP)
        {
            "name": "proxy only (auto client)",
            "proxy": proxy,
            "cookies": None,
            "clients": None,
        },
        {
            "name": "proxy + cookies (auto client)",
            "proxy": proxy,
            "cookies": cookies_file,
            "clients": None,
        },
        {
            "name": "direct (auto client)",
            "proxy": None,
            "cookies": None,
            "clients": None,
        },
        {
            "name": "direct + cookies (auto client)",
            "proxy": None,
            "cookies": cookies_file,
            "clients": None,
        },
        # ===== Specific client fallbacks =====
        {
            "name": "proxy + ios client",
            "proxy": proxy,
            "cookies": None,
            "clients": ["ios"],
        },
        {
            "name": "proxy + mweb client",
            "proxy": proxy,
            "cookies": None,
            "clients": ["mweb"],
        },
        {
            "name": "direct + ios client",
            "proxy": None,
            "cookies": None,
            "clients": ["ios"],
        },
        {
            "name": "direct + mweb client",
            "proxy": None,
            "cookies": None,
            "clients": ["mweb"],
        },
    ]

    last_error = ""
    for strategy in strategies:
        try:
            logger.info(f"[Strategy] {strategy['name']}")
            ydl_opts = build_ydl_opts(
                upload_dir=upload_dir,
                unique_id=unique_id,
                proxy=strategy["proxy"],
                cookies_file=strategy["cookies"],
                player_clients=strategy["clients"],
            )
            downloaded_path = safe_download(ydl_opts, url)
            basename = os.path.basename(downloaded_path)
            logger.info(f"Download succeeded via {strategy['name']}: {downloaded_path}")
            return {"status": "success", "filepath": downloaded_path, "filename": basename}
        except Exception as e:
            err_str = str(e)
            logger.warning(f"Strategy '{strategy['name']}' failed: {err_str[:200]}")
            last_error = err_str
            time.sleep(1)

    # ===== Strategy 7: Invidious fallback =====
    logger.info("[Strategy] Trying Invidious fallback...")
    invidious_path = try_with_invidious(url, upload_dir, unique_id)
    if invidious_path:
        basename = os.path.basename(invidious_path)
        return {"status": "success", "filepath": invidious_path, "filename": basename}

    # ===== All strategies exhausted =====
    err_lower = last_error.lower()
    if "sign in" in err_lower or "bot" in err_lower or "403" in err_lower:
        raise HTTPException(
            status_code=503,
            detail=(
                "❌ YouTube يمنع التنزيل من هذا الخادم. الحلول المتاحة:\n"
                "1. ضع ملف cookies.txt في المسار YOUTUBE_COOKIES_PATH\n"
                "2. استخدم وكيل (proxy) سكني/residential\n"
                "3. قم بتنزيل المحاضرة على جهازك ورفعها كملف صوتي مباشرة."
            ),
        )

    raise HTTPException(status_code=500, detail=f"All download strategies failed. Last error: {last_error[:500]}")
