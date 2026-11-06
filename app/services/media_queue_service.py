"""
Media Queue Service:
Dispatches heavy audio/media preprocessing jobs to the Go Microservice via Redis Queue,
and streams real-time progress events using Redis Pub/Sub.
Provides graceful fallback and sanitized user-safe messaging.
"""

import os
import json
import asyncio
from typing import List, Optional, Dict, Any, AsyncGenerator
import redis.asyncio as aioredis
from app.core.logger import logger

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = "media:jobs:queue"


class MediaQueueUnavailableError(Exception):
    """Internal infrastructure error for Redis/Go media queue (does not leak URLs to users)."""
    def __init__(self, message: str = "خدمة معالجة الوسائط غير متصلة"):
        super().__init__(message)
        self.message = message


class MediaQueueService:
    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._last_failure: float = 0.0

    async def get_redis(self) -> aioredis.Redis:
        """
        Connects to Redis with smart candidate discovery and fast-fail circuit breaker.
        Raises sanitized MediaQueueUnavailableError if connection fails.
        """
        # Circuit breaker: don't block repeatedly if Redis failed recently
        now = asyncio.get_event_loop().time()
        if self._redis is None and (now - self._last_failure) < 30.0:
            raise MediaQueueUnavailableError("خدمة طابور الوسائط غير متصلة حالياً (تفعيل المعالجة المباشرة).")

        if self._redis is not None:
            try:
                await self._redis.ping()
                return self._redis
            except Exception:
                self._redis = None

        candidate_urls = [
            self.redis_url,
            os.getenv("REDIS_URL", ""),
            "redis://redis:6379/0",
            "redis://127.0.0.1:6379/0",
            "redis://localhost:6379/0"
        ]
        # Remove duplicates while preserving order
        seen = set()
        clean_urls = []
        for u in candidate_urls:
            if u and u not in seen:
                seen.add(u)
                clean_urls.append(u)

        last_err = None
        for candidate in clean_urls:
            try:
                client = aioredis.from_url(
                    candidate,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=1.0,
                )
                await client.ping()
                logger.info(f"⚡ [REDIS] Connected successfully to Redis at {candidate}")
                self._redis = client
                self.redis_url = candidate
                return self._redis
            except Exception as e:
                last_err = e
                continue

        self._redis = None
        self._last_failure = now
        logger.info(f"ℹ️ [MEDIA_QUEUE] Redis unreachable locally ({last_err}), using high-speed in-process direct pipeline.")
        raise MediaQueueUnavailableError("تعذر الاتصال بخدمة طابور الوسائط.") from last_err

    async def is_available(self) -> bool:
        """Checks if Redis media queue is reachable without throwing."""
        try:
            r = await self.get_redis()
            await r.ping()
            return True
        except Exception:
            return False

    async def dispatch_and_stream(
        self,
        job_id: str,
        user_id: str,
        task_type: str,
        audio_paths: List[str],
        pdf_path: Optional[str] = None,
        chunk_seconds: int = 180,
        output_dir: Optional[str] = None,
        timeout_seconds: int = 600,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Dispatches media preprocessing job to Go worker via Redis,
        and yields real-time progress events via Redis Pub/Sub.
        Yields progress dicts until completed, yielding the final result dict last.
        """
        r = await self.get_redis()

        if not output_dir:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            output_dir = os.path.normpath(os.path.join(base_dir, "uploads", "processed"))

        os.makedirs(output_dir, exist_ok=True)

        job_payload = {
            "job_id": job_id,
            "user_id": user_id,
            "task_type": task_type,
            "audio_paths": audio_paths,
            "pdf_path": pdf_path or "",
            "chunk_seconds": chunk_seconds,
            "output_dir": output_dir,
        }

        channel_name = f"media:progress:{job_id}"
        result_key = f"media:result:{job_id}"

        # 1. Subscribe to Pub/Sub channel BEFORE pushing job to avoid missing early events
        pubsub = r.pubsub()
        await pubsub.subscribe(channel_name)
        logger.info(f"⚡ [MEDIA_QUEUE] Subscribed to {channel_name}, pushing job {job_id}...")

        try:
            # 2. Push job to Go worker queue
            await r.lpush(QUEUE_KEY, json.dumps(job_payload))

            # 3. Stream progress events with timeout
            start_time = asyncio.get_event_loop().time()
            completed = False

            while not completed:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    raise TimeoutError("استغرقت معالجة التسجيلات الصوتية وقتاً أطول من المتوقع.")

                try:
                    # Get message from pubsub (with 1.5s timeout to allow cancellation/heartbeats)
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.5)
                    if message and message.get("type") == "message":
                        data_str = message.get("data")
                        if data_str:
                            event_data = json.loads(data_str)
                            yield event_data

                            status = event_data.get("status")
                            if status == "completed":
                                completed = True
                                break
                            elif status == "failed":
                                err_msg = event_data.get("message") or "فشلت معالجة التسجيلات الصوتية"
                                raise RuntimeError(err_msg)

                    # Also check if result key was already set in Redis
                    result_raw = await r.get(result_key)
                    if result_raw:
                        result_data = json.loads(result_raw)
                        if result_data.get("status") == "completed":
                            completed = True
                            break
                        elif result_data.get("status") == "failed":
                            raise RuntimeError(result_data.get("error") or "فشلت معالجة التسجيلات الصوتية")

                except asyncio.CancelledError:
                    logger.warning(f"⚠️ [MEDIA_QUEUE] Streaming cancelled for job {job_id}")
                    raise

            # 4. Fetch final complete result
            final_raw = await r.get(result_key)
            if not final_raw:
                raise RuntimeError("لم تكتمل معالجة التسجيلات الصوتية بشكل صحيح")

            final_result = json.loads(final_raw)
            final_result["type"] = "final_result"
            yield final_result

        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.close()
            logger.info(f"🧹 [MEDIA_QUEUE] Cleaned up subscription for {channel_name}")


# Global singleton instance
media_queue = MediaQueueService()
