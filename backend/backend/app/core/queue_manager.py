"""
نظام طابور ذكي بمستوى Anthropic
Priority Queue System with:
- Priority levels based on subscription tier
- Real-time SSE position updates
- Rate limiting per user
- Retry with exponential backoff
- Concurrent processing limits
- Job lifecycle management
"""

import asyncio
import uuid
import time
from datetime import datetime, timezone
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable
from collections import OrderedDict
from app.core.logger import logger


class JobPriority(IntEnum):
    """Lower number = higher priority (processed first)."""
    VIP = 1          # أسبوعي/شهري VIP
    ADVANCED = 2     # أسبوعي/شهري متقدم
    BASIC = 3        # أسبوعي/شهري أساسي/طالب
    FREE_TRIAL = 5   # تجربة مجانية


class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


@dataclass(order=True)
class Job:
    """A single processing job in the queue."""
    priority: int
    created_at: float = field(compare=True)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    user_id: str = field(default="", compare=False)
    status: str = field(default=JobStatus.QUEUED, compare=False)
    progress: int = field(default=0, compare=False)  # 0-100
    position: int = field(default=0, compare=False)
    retry_count: int = field(default=0, compare=False)
    max_retries: int = field(default=3, compare=False)
    result: Optional[Dict[str, Any]] = field(default=None, compare=False)
    error: Optional[str] = field(default=None, compare=False)
    payload: Dict[str, Any] = field(default_factory=dict, compare=False)
    
    # Timestamps
    started_at: Optional[float] = field(default=None, compare=False)
    completed_at: Optional[float] = field(default=None, compare=False)
    estimated_wait_seconds: int = field(default=0, compare=False)


class QueueManager:
    """Enterprise-grade job queue manager."""
    
    # Processing config
    MAX_CONCURRENT_JOBS = 2        # عدد المهام المتزامنة
    MAX_JOBS_PER_USER = 1          # حد المهام لكل مستخدم
    AVG_JOB_DURATION_SECONDS = 90  # متوسط مدة المعالجة
    MAX_RETRY_DELAY = 60           # أقصى تأخير لإعادة المحاولة
    JOB_TTL_SECONDS = 3600         # مدة حفظ المهمة المنتهية (ساعة)
    
    def __init__(self):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._user_active_jobs: Dict[str, str] = {}  # user_id -> job_id
        self._processing_count = 0
        self._workers_started = False
        self._lock = asyncio.Lock()
        self._job_events: Dict[str, asyncio.Event] = {}  # Notify SSE listeners
        self._total_processed = 0
        self._total_failed = 0
        logger.info("[INIT] تم تهيئة نظام الطابور الذكي")
    
    async def start_workers(self):
        """Start background worker tasks."""
        if self._workers_started:
            return
        self._workers_started = True
        for i in range(self.MAX_CONCURRENT_JOBS):
            asyncio.create_task(self._worker(f"worker-{i+1}"))
        asyncio.create_task(self._cleanup_worker())
        logger.info(f"[CONFIG] تم تشغيل {self.MAX_CONCURRENT_JOBS} عمال معالجة + عامل تنظيف")
    
    def get_priority_for_plan(self, plan_name: str) -> JobPriority:
        """Map subscription plan name to queue priority."""
        name_lower = plan_name.lower() if plan_name else ""
        if "vip" in name_lower or "غير محدود" in plan_name:
            return JobPriority.VIP
        elif "متقدم" in plan_name or "advanced" in name_lower:
            return JobPriority.ADVANCED
        elif "طالب" in plan_name or "أساسي" in plan_name or "basic" in name_lower or "student" in name_lower:
            return JobPriority.BASIC
        return JobPriority.FREE_TRIAL
    
    async def enqueue(
        self, 
        user_id: str,
        payload: Dict[str, Any],
        plan_name: str = ""
    ) -> Job:
        """Add a job to the queue. Returns the job with position info."""
        
        # Check per-user limit
        if user_id in self._user_active_jobs:
            existing_job_id = self._user_active_jobs[user_id]
            existing = self._jobs.get(existing_job_id)
            if existing and existing.status in (JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.STREAMING):
                raise ValueError(
                    "⏳ لديك طلب قيد المعالجة بالفعل. يرجى الانتظار حتى ينتهي."
                )
        
        priority = self.get_priority_for_plan(plan_name)
        
        job = Job(
            priority=priority,
            created_at=time.time(),
            user_id=user_id,
            payload=payload
        )
        
        async with self._lock:
            self._jobs[job.job_id] = job
            self._user_active_jobs[user_id] = job.job_id
            self._job_events[job.job_id] = asyncio.Event()
            await self._queue.put(job)
            self._update_positions()
        
        logger.info(
            f"[INCOMING] مهمة جديدة: {job.job_id[:8]}... | "
            f"المستخدم: {user_id[:8]}... | "
            f"الأولوية: {JobPriority(priority).name} | "
            f"الموقع: #{job.position}"
        )
        
        # Start workers if not started
        await self.start_workers()
        
        return job
    
    def _update_positions(self):
        """Recalculate queue positions for all waiting jobs."""
        queued_jobs = sorted(
            [j for j in self._jobs.values() if j.status == JobStatus.QUEUED],
            key=lambda j: (j.priority, j.created_at)
        )
        for i, job in enumerate(queued_jobs):
            job.position = i + 1
            job.estimated_wait_seconds = (i + 1) * self.AVG_JOB_DURATION_SECONDS
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        return self._jobs.get(job_id)
    
    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get serializable job status."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status,
            "position": job.position,
            "progress": job.progress,
            "estimated_wait_seconds": job.estimated_wait_seconds,
            "priority": JobPriority(job.priority).name,
            "retry_count": job.retry_count,
            "error": job.error,
            "created_at": datetime.fromtimestamp(job.created_at, tz=timezone.utc).isoformat(),
            "started_at": datetime.fromtimestamp(job.started_at, tz=timezone.utc).isoformat() if job.started_at else None,
            "completed_at": datetime.fromtimestamp(job.completed_at, tz=timezone.utc).isoformat() if job.completed_at else None,
        }
    
    def get_queue_stats(self) -> Dict:
        """Get overall queue statistics."""
        queued = sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED)
        processing = sum(1 for j in self._jobs.values() if j.status in (JobStatus.PROCESSING, JobStatus.STREAMING))
        return {
            "queued": queued,
            "processing": processing,
            "max_concurrent": self.MAX_CONCURRENT_JOBS,
            "total_processed": self._total_processed,
            "total_failed": self._total_failed
        }
    
    async def _worker(self, worker_name: str):
        """Background worker that processes jobs from the queue."""
        logger.info(f"[WORKER] العامل {worker_name} جاهز للعمل")
        
        while True:
            try:
                job = await self._queue.get()
                
                # Skip cancelled/completed jobs
                if job.status in (JobStatus.CANCELLED, JobStatus.COMPLETED):
                    self._queue.task_done()
                    continue
                
                async with self._lock:
                    self._processing_count += 1
                    job.status = JobStatus.PROCESSING
                    job.started_at = time.time()
                    job.position = 0
                    self._update_positions()
                    self._notify_job(job.job_id)
                
                logger.info(f"[PROCESSING] {worker_name} يعالج: {job.job_id[:8]}...")
                
                try:
                    # Execute the actual processing
                    processor = job.payload.get("_processor")
                    if processor and callable(processor):
                        await processor(job)
                    
                    async with self._lock:
                        job.status = JobStatus.COMPLETED
                        job.completed_at = time.time()
                        job.progress = 100
                        self._total_processed += 1
                        self._notify_job(job.job_id)
                    
                    duration = job.completed_at - job.started_at
                    logger.info(f"[SUCCESS] {worker_name} أكمل: {job.job_id[:8]}... ({duration:.1f}s)")
                    
                except Exception as e:
                    await self._handle_failure(job, str(e), worker_name)
                
                finally:
                    async with self._lock:
                        self._processing_count -= 1
                        if job.user_id in self._user_active_jobs:
                            if self._user_active_jobs[job.user_id] == job.job_id:
                                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                                    del self._user_active_jobs[job.user_id]
                    self._queue.task_done()
                    
            except Exception as e:
                logger.error(f"[ERROR] خطأ في {worker_name}: {str(e)}", exc_info=True)
                await asyncio.sleep(1)
    
    async def _handle_failure(self, job: Job, error: str, worker_name: str):
        """Handle job failure with exponential backoff retry."""
        async with self._lock:
            job.retry_count += 1
            
            if job.retry_count <= job.max_retries:
                delay = min(2 ** job.retry_count, self.MAX_RETRY_DELAY)
                job.status = JobStatus.RETRYING
                job.error = f"محاولة إعادة {job.retry_count}/{job.max_retries}: {error}"
                self._notify_job(job.job_id)
                
                logger.warning(
                    f"[RETRY] {worker_name} يعيد المحاولة: {job.job_id[:8]}... "
                    f"({job.retry_count}/{job.max_retries}) بعد {delay}s"
                )
                
                await asyncio.sleep(delay)
                job.status = JobStatus.QUEUED
                await self._queue.put(job)
            else:
                job.status = JobStatus.FAILED
                job.error = f"فشلت المعالجة بعد {job.max_retries} محاولات: {error}"
                job.completed_at = time.time()
                self._total_failed += 1
                self._notify_job(job.job_id)
                
                logger.error(f"[FATAL] {worker_name} فشل نهائي: {job.job_id[:8]}... - {error}")
    
    def _notify_job(self, job_id: str):
        """Signal SSE listeners that job status changed."""
        event = self._job_events.get(job_id)
        if event:
            event.set()
    
    async def wait_for_update(self, job_id: str, timeout: float = 30) -> bool:
        """Wait for a job status update (used by SSE endpoint)."""
        event = self._job_events.get(job_id)
        if not event:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False
    
    async def cancel_job(self, job_id: str, user_id: str) -> bool:
        """Cancel a queued job."""
        job = self._jobs.get(job_id)
        if not job or job.user_id != user_id:
            return False
        if job.status != JobStatus.QUEUED:
            return False
        
        async with self._lock:
            job.status = JobStatus.CANCELLED
            if user_id in self._user_active_jobs:
                del self._user_active_jobs[user_id]
            self._update_positions()
            self._notify_job(job_id)
        
        logger.info(f"[CANCEL] تم إلغاء المهمة: {job_id[:8]}...")
        return True
    
    async def _cleanup_worker(self):
        """Periodically clean up old completed/failed jobs."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            now = time.time()
            async with self._lock:
                expired = [
                    jid for jid, j in self._jobs.items()
                    if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
                    and j.completed_at
                    and (now - j.completed_at) > self.JOB_TTL_SECONDS
                ]
                for jid in expired:
                    del self._jobs[jid]
                    self._job_events.pop(jid, None)
                
                if expired:
                    logger.info(f"[CLEANUP] تم تنظيف {len(expired)} مهمة منتهية")


# Singleton instance
queue_manager = QueueManager()
