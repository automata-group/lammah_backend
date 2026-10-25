import os
import shutil
import time
import asyncio
from typing import Optional
from app.core.logger import logger

def safe_copy_file(src_path: str, dest_path: str, max_retries: int = 8, retry_delay: float = 0.5) -> str:
    """
    Safely copies a file from source to destination with retry logic.
    Handles Windows file lock (WinError 32) and concurrent write streams between Go uploader and Python.
    """
    last_err = None
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    
    for attempt in range(max_retries):
        try:
            if not os.path.exists(src_path):
                time.sleep(retry_delay)
                continue
            
            # Check if file is still growing (actively being written)
            size1 = os.path.getsize(src_path)
            time.sleep(0.1)
            size2 = os.path.getsize(src_path)
            if size1 != size2:
                # File is still being written to disk
                time.sleep(retry_delay)
                continue

            # Attempt standard copy
            shutil.copy2(src_path, dest_path)
            
            src_size = os.path.getsize(src_path)
            dst_size = os.path.getsize(dest_path)
            if src_size == 0 or dst_size == src_size:
                logger.info(f"✅ [FILE_MANAGER] Safely copied: {src_path} -> {dest_path} ({dst_size} bytes)")
                return dest_path
        except (PermissionError, OSError) as e:
            last_err = e
            logger.warning(f"⏳ [FILE_LOCK] Retrying file copy ({attempt + 1}/{max_retries}) for {os.path.basename(src_path)}: {e}")
            time.sleep(retry_delay * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(retry_delay)

    # Fallback byte-stream copy in case copy2 was blocked by metadata lock
    try:
        with open(src_path, "rb") as fsrc:
            with open(dest_path, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
        logger.info(f"✅ [FILE_MANAGER] Fallback stream copy succeeded: {dest_path}")
        return dest_path
    except Exception as e:
        logger.error(f"❌ [FILE_LOCK] Failed to safely copy {src_path} -> {dest_path}: {e}")
        if last_err:
            raise last_err
        raise e

async def safe_copy_file_async(src_path: str, dest_path: str, max_retries: int = 8, retry_delay: float = 0.5) -> str:
    """Async wrapper for safe_copy_file."""
    return await asyncio.to_thread(safe_copy_file, src_path, dest_path, max_retries, retry_delay)
