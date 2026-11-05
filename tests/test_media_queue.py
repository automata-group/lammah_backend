import sys
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure backend directory is in python path
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-supabase-key-for-unit-tests")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("OPENROUTER_API_KEY", "dummy-openrouter-key")

from app.services.media_queue_service import (
    MediaQueueService,
    MediaQueueUnavailableError
)


def test_media_queue_sanitized_error_handling():
    """Verify that Redis connection failures raise MediaQueueUnavailableError without leaking raw internal URLs to users."""
    service = MediaQueueService(redis_url="redis://invalid-non-existent-host:9999/0")

    async def _test():
        with pytest.raises(MediaQueueUnavailableError) as exc_info:
            await service.get_redis()
        # Message should be clean and not leak the raw url
        msg = str(exc_info.value)
        assert "redis://invalid-non-existent-host:9999/0" not in msg
        assert "تعذر الاتصال" in msg

        # is_available returns False cleanly without throwing
        available = await service.is_available()
        assert available is False

    asyncio.run(_test())


def test_media_queue_pubsub_dispatch_and_stream_mock():
    """Verify that dispatch_and_stream correctly streams events and retrieves final result."""
    service = MediaQueueService(redis_url="redis://localhost:6379/0")

    mock_redis = AsyncMock()
    mock_pubsub = AsyncMock()

    # Sequence of pubsub messages followed by completion
    events = [
        {"type": "message", "data": '{"job_id": "test_job", "status": "processing", "percent": 25, "message": "جاري التحويل"}'},
        {"type": "message", "data": '{"job_id": "test_job", "status": "completed", "percent": 100, "message": "اكتملت المعالجة"}'},
    ]

    async def fake_get_message(*args, **kwargs):
        if events:
            return events.pop(0)
        return None

    mock_pubsub.get_message = fake_get_message
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    mock_redis.get.return_value = '{"job_id": "test_job", "status": "completed", "total_duration": 180.0, "chunks": []}'

    service._redis = mock_redis

    async def _run_stream():
        received_events = []
        async for event in service.dispatch_and_stream(
            job_id="test_job",
            user_id="user_123",
            task_type="batch_audio",
            audio_paths=["test1.mp3"],
            chunk_seconds=180
        ):
            received_events.append(event)
        return received_events

    streamed = asyncio.run(_run_stream())
    assert len(streamed) >= 2
    assert streamed[0]["percent"] == 25
    assert streamed[-1]["type"] == "final_result"
    assert streamed[-1]["total_duration"] == 180.0
