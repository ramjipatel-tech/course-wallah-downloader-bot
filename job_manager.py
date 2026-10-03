import os
import time
import shutil
import asyncio
import logging
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any, Callable, Union

from vars import (
    DOWNLOAD_WORKERS,
    UPLOAD_WORKERS,
    MAX_ACTIVE_USERS,
    MAX_JOBS_PER_USER,
    TEMP_DIR,
    WATERMARK_TEXT,
    WATERMARK_FILE,
    CREDIT
)
from db import db
from utils import JobProgressTracker, hrb, hrt

logger = logging.getLogger("CourseWallahBot")


@dataclass
class JobCheckpoint:
    job_id: str
    user_id: int
    chat_id: int
    channel_id: int | str
    source_filename: str
    total_items: int
    bot_id: str = "bot_1"
    bot_username: str = ""
    current_index: int = 0  # 0-based index
    completed_indices: List[int] = field(default_factory=list)
    failed_indices: List[int] = field(default_factory=list)
    current_item_title: str = ""
    phase: str = "QUEUED"  # QUEUED, DOWNLOADING, PROCESSING, UPLOADING, DONE
    status: str = "QUEUED"  # QUEUED, DOWNLOADING, PROCESSING, UPLOADING, PAUSED, CANCELLED, COMPLETED, FAILED, RECOVERING
    batch_name: str = ""
    quality: str = "720p"
    watermark: str = "/d"
    credit: str = CREDIT
    pw_token: str = "/d"
    thumb: str = "/d"
    active_thread_id: Optional[int] = None
    temp_dir: str = ""
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    raw_content: str = ""  # Store text content for restart recovery
    item_results: List[Dict[str, Any]] = field(default_factory=list)
    uploaded_parts: Dict[str, List[int]] = field(default_factory=dict)  # item_idx_str -> list of uploaded part numbers

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobCheckpoint":
        # Filter only known fields
        known = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in known}
        if "user_id" not in filtered:
            filtered["user_id"] = 0
        if "chat_id" not in filtered:
            filtered["chat_id"] = filtered.get("user_id", 0)
        if "channel_id" not in filtered:
            filtered["channel_id"] = filtered.get("chat_id", 0)
        if "source_filename" not in filtered:
            filtered["source_filename"] = "course.txt"
        if "total_items" not in filtered:
            filtered["total_items"] = 0
        return cls(**filtered)


class JobManager:
    """
    Concurrent job coordinator, checkpoint engine, and multi-bot restart recovery manager.
    Maintains strict bot_id + user_id isolation while keeping global machine resources bounded.
    """

    _download_semaphore: Optional[asyncio.Semaphore] = None
    _upload_semaphore: Optional[asyncio.Semaphore] = None
    _user_semaphores: Dict[str, asyncio.Semaphore] = {}

    def __init__(self, bot_id: Optional[str] = "bot_1"):
        self.bot_id = str(bot_id).strip().lower() if bot_id else "bot_1"

        # Per-bot isolated in-memory maps
        self.active_tasks: Dict[str, asyncio.Task] = {}  # job_id -> asyncio.Task
        self.user_active_jobs: Dict[str, str] = {}  # "bot_id:user_id" -> job_id
        self.pause_events: Dict[str, asyncio.Event] = {}  # job_id -> event
        self.cancel_flags: Dict[str, bool] = {}  # job_id -> bool
        self.cancel_reasons: Dict[str, str] = {}  # job_id -> cancel reason string
        self.progress_trackers: Dict[str, JobProgressTracker] = {}

    @classmethod
    def get_download_semaphore(cls) -> asyncio.Semaphore:
        if cls._download_semaphore is None:
            cls._download_semaphore = asyncio.Semaphore(DOWNLOAD_WORKERS)
        return cls._download_semaphore

    @classmethod
    def get_upload_semaphore(cls) -> asyncio.Semaphore:
        if cls._upload_semaphore is None:
            cls._upload_semaphore = asyncio.Semaphore(UPLOAD_WORKERS)
        return cls._upload_semaphore

    @property
    def download_semaphore(self) -> asyncio.Semaphore:
        return self.get_download_semaphore()

    @property
    def upload_semaphore(self) -> asyncio.Semaphore:
        return self.get_upload_semaphore()

    def _user_key(self, user_id: int, bot_id: Optional[str] = None) -> str:
        b_id = str(bot_id or self.bot_id).strip().lower()
        return f"{b_id}:{user_id}"

    def get_user_semaphore(self, user_id: int, bot_id: Optional[str] = None) -> asyncio.Semaphore:
        k = self._user_key(user_id, bot_id)
        if k not in JobManager._user_semaphores:
            JobManager._user_semaphores[k] = asyncio.Semaphore(MAX_JOBS_PER_USER)
        return JobManager._user_semaphores[k]

    def create_job(
        self,
        user_id: int,
        chat_id: int,
        channel_id: int | str,
        source_filename: str,
        raw_content: str,
        total_items: int,
        start_index: int = 1,
        batch_name: str = "",
        quality: str = "720p",
        watermark: str = "/d",
        credit: str = CREDIT,
        pw_token: str = "/d",
        thumb: str = "/d",
        active_thread_id: Optional[int] = None,
        bot_id: Optional[str] = None,
        bot_username: str = ""
    ) -> JobCheckpoint:
        effective_bot_id = str(bot_id or self.bot_id).strip().lower()
        job_id = f"job_{uuid.uuid4().hex[:8].upper()}"
        job_temp_dir = os.path.join(TEMP_DIR, str(user_id), f"{effective_bot_id}_{job_id}")
        os.makedirs(job_temp_dir, exist_ok=True)
        os.makedirs(os.path.join(job_temp_dir, "downloads"), exist_ok=True)
        os.makedirs(os.path.join(job_temp_dir, "processed"), exist_ok=True)
        os.makedirs(os.path.join(job_temp_dir, "upload"), exist_ok=True)

        job = JobCheckpoint(
            job_id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            channel_id=channel_id,
            source_filename=source_filename,
            total_items=total_items,
            bot_id=effective_bot_id,
            bot_username=bot_username,
            current_index=max(0, start_index - 1),
            batch_name=batch_name,
            quality=quality,
            watermark=watermark,
            credit=credit,
            pw_token=pw_token,
            thumb=thumb,
            active_thread_id=active_thread_id,
            temp_dir=job_temp_dir,
            raw_content=raw_content,
            status="QUEUED"
        )

        db.save_job(job.to_dict())
        return job

    def is_user_busy(self, user_id: int, bot_id: Optional[str] = None) -> bool:
        """Checks if user has an active download strictly on this bot."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        k = self._user_key(user_id, b_id)
        if k in self.user_active_jobs:
            job_id = self.user_active_jobs[k]
            task = self.active_tasks.get(job_id)
            if task:
                if not task.done():
                    return True
                else:
                    self.user_active_jobs.pop(k, None)
                    self.active_tasks.pop(job_id, None)
                    return False
            return True
        # Direct user_id integer key fallback only if queried on this manager's bot_id
        if (bot_id is None or str(bot_id).strip().lower() == str(self.bot_id).strip().lower()) and user_id in self.user_active_jobs:
            job_id = self.user_active_jobs[user_id]
            task = self.active_tasks.get(job_id)
            if task:
                if not task.done():
                    return True
                else:
                    self.user_active_jobs.pop(user_id, None)
                    self.active_tasks.pop(job_id, None)
                    return False
            return True
        return False

    def get_active_job_id(self, user_id: int, bot_id: Optional[str] = None) -> Optional[str]:
        b_id = str(bot_id or self.bot_id).strip().lower()
        k = self._user_key(user_id, b_id)
        if k in self.user_active_jobs:
            return self.user_active_jobs[k]
        if (bot_id is None or str(bot_id).strip().lower() == str(self.bot_id).strip().lower()):
            return self.user_active_jobs.get(user_id)
        return None

    def set_user_active_job(self, user_id: int, job_id: str, bot_id: Optional[str] = None):
        """Sets active job mapping for a user strictly on this bot."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        k = self._user_key(user_id, b_id)
        self.user_active_jobs[k] = job_id

    def clear_user_active_job(self, user_id: int, bot_id: Optional[str] = None):
        """Clears active job mapping for a user strictly on this bot."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        k = self._user_key(user_id, b_id)
        self.user_active_jobs.pop(k, None)
        if (bot_id is None or str(bot_id).strip().lower() == str(self.bot_id).strip().lower()):
            self.user_active_jobs.pop(user_id, None)

    def pause_job(
        self,
        user_id: int,
        job_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        reason: str = "User /stop command"
    ) -> tuple[bool, str, Optional[JobCheckpoint]]:
        """Pauses a specific job on a specific bot without affecting any other running jobs."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        target_job_id = job_id or self.get_active_job_id(user_id, bot_id=b_id)
        if not target_job_id:
            db_job = db.get_user_active_job(user_id, bot_id=b_id)
            if db_job:
                target_job_id = db_job["job_id"]
            else:
                return False, "No active download found to pause.", None

        # Set job-specific pause flag and reason
        self.cancel_flags[target_job_id] = True
        self.cancel_reasons[target_job_id] = reason

        job_data = db.get_job(target_job_id, bot_id=b_id)
        if job_data:
            job = JobCheckpoint.from_dict(job_data)
            job.status = "PAUSED"
            job.phase = "PAUSED"
            db.save_job(job.to_dict())
        else:
            job = None

        # Cancel ONLY the target job's asyncio task
        task = self.active_tasks.get(target_job_id)
        if task and not task.done():
            task.cancel()

        k = self._user_key(user_id, b_id)
        self.user_active_jobs.pop(k, None)

        print(f"[APPX {target_job_id}] CANCEL REQUESTED: {reason}")
        logging.info(f"[APPX {target_job_id}] CANCEL REQUESTED: {reason}")
        return True, "Download paused successfully.", job

    def cancel_job(
        self,
        user_id: int,
        job_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        reason: str = "User /cancel command"
    ) -> tuple[bool, str]:
        """Cancels a specific job and purges temp resources without affecting other jobs."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        target_job_id = job_id or self.get_active_job_id(user_id, bot_id=b_id)
        job_data = None

        if not target_job_id:
            db_job = db.get_user_active_job(user_id, bot_id=b_id)
            if not db_job:
                db_job = db.get_user_paused_job(user_id, bot_id=b_id)
            if db_job:
                target_job_id = db_job["job_id"]
                job_data = db_job
            else:
                return False, "No active or paused job found to cancel."
        else:
            job_data = db.get_job(target_job_id, bot_id=b_id)

        # Set job-specific cancel flag and reason
        self.cancel_flags[target_job_id] = True
        self.cancel_reasons[target_job_id] = reason

        # Cancel ONLY the target job's asyncio task
        task = self.active_tasks.get(target_job_id)
        if task and not task.done():
            task.cancel()

        # Update DB status & cleanup
        if job_data:
            job = JobCheckpoint.from_dict(job_data)
            job.status = "CANCELLED"
            job.phase = "CANCELLED"
            db.save_job(job.to_dict())

            if job.temp_dir and os.path.exists(job.temp_dir):
                try:
                    shutil.rmtree(job.temp_dir, ignore_errors=True)
                except Exception:
                    pass

        k = self._user_key(user_id, b_id)
        self.user_active_jobs.pop(k, None)
        self.active_tasks.pop(target_job_id, None)

        print(f"[APPX {target_job_id}] CANCEL REQUESTED: {reason}")
        logging.info(f"[APPX {target_job_id}] CANCEL REQUESTED: {reason}")
        return True, "Job permanently cancelled and cleaned up."

    def pause_all(self, bot_id: Optional[str] = None):
        """Pauses all active jobs for a specific bot."""
        b_id = str(bot_id or self.bot_id).strip().lower()
        active_uids = list(self.user_active_jobs.keys())
        for k in active_uids:
            if str(k).startswith(f"{b_id}:"):
                uid = int(str(k).split(":")[-1])
                self.pause_job(uid, bot_id=b_id)

    async def recover_on_startup(self, bot_clients: Union[Any, List[Any], Dict[str, Any]], run_worker_fn: Callable) -> int:
        """
        Scans for unfinished jobs after bot restart/crash and resumes them
        using the appropriate bot client instance.
        """
        unfinished = db.get_unfinished_jobs()
        if not unfinished:
            print("[RECOVERY] No unfinished jobs found. Clean startup.")
            return 0

        print(f"[RECOVERY] Found {len(unfinished)} unfinished job(s) from previous session.")
        recovered_count = 0

        # Build client lookup map
        client_map: Dict[str, Any] = {}
        default_client = None

        if isinstance(bot_clients, dict):
            client_map = {str(k).lower(): v for k, v in bot_clients.items()}
            default_client = next(iter(bot_clients.values()), None)
        elif isinstance(bot_clients, (list, tuple)):
            for c in bot_clients:
                if c is not None:
                    if default_client is None:
                        default_client = c
                    if hasattr(c, "ctx") and c.ctx and getattr(c.ctx, "bot_id", None):
                        client_map[str(c.ctx.bot_id).lower()] = c
                    if hasattr(c, "name") and c.name:
                        client_map[str(c.name).lower()] = c
                    if hasattr(c, "me") and c.me and getattr(c.me, "username", None):
                        client_map[str(c.me.username).lower()] = c
        else:
            default_client = bot_clients

        for j_dict in unfinished:
            job = JobCheckpoint.from_dict(j_dict)
            job_id = job.job_id
            user_id = job.user_id

            # Validate checkpoint
            if job.current_index >= job.total_items:
                job.status = "COMPLETED"
                db.save_job(job.to_dict())
                continue

            # Resolve the correct bot client for this job
            target_client = (
                client_map.get(str(job.bot_id).lower())
                or (client_map.get(str(job.bot_username).lower()) if job.bot_username else None)
            )

            if not target_client:
                # Do not recover jobs for bots that are offline or not configured
                logger.info(f"[RECOVERY] Skipping job {job_id} because bot '{job.bot_id}' is not online.")
                continue

            job.status = "RECOVERING"
            db.save_job(job.to_dict())

            print(f"[RECOVERY] Resuming job {job_id} on {job.bot_id} for user {user_id} (Progress: {len(job.completed_indices)}/{job.total_items} items)...")
            logging.info(f"[RECOVERY] Resuming job {job_id} on {job.bot_id} for user {user_id} (Progress: {len(job.completed_indices)}/{job.total_items} items)")

            # Resolve bot-specific job manager if client has ctx
            target_jm = getattr(getattr(target_client, "ctx", None), "job_manager", None) or self

            # Launch background worker for resumption
            target_jm.cancel_flags[job_id] = False
            target_jm.cancel_reasons.pop(job_id, None)
            task = asyncio.create_task(target_jm._execute_job_wrapper(job, target_client, run_worker_fn))
            target_jm.active_tasks[job_id] = task

            k = target_jm._user_key(user_id, job.bot_id)
            target_jm.user_active_jobs[k] = job_id
            recovered_count += 1

        return recovered_count

    async def launch_job(self, job: JobCheckpoint, bot_client, run_worker_fn: Callable):
        job_id = job.job_id
        k = self._user_key(job.user_id, job.bot_id)
        self.user_active_jobs[k] = job_id
        self.cancel_flags[job_id] = False
        self.cancel_reasons.pop(job_id, None)
        task = asyncio.create_task(self._execute_job_wrapper(job, bot_client, run_worker_fn))
        self.active_tasks[job_id] = task
        return task

    async def _execute_job_wrapper(self, job: JobCheckpoint, bot_client, run_worker_fn: Callable):
        job_id = job.job_id
        user_sem = self.get_user_semaphore(job.user_id, job.bot_id)
        async with user_sem:
            async with self.download_semaphore:
                try:
                    job.status = "DOWNLOADING"
                    job.phase = "DOWNLOADING"
                    db.save_job(job.to_dict())

                    # Call actual processing pipeline
                    await run_worker_fn(job, bot_client, self)

                    # Mark completed if reached end normally
                    job_data = db.get_job(job_id, bot_id=job.bot_id)
                    if job_data and job_data.get("status") not in ("CANCELLED", "PAUSED", "FAILED"):
                        job.status = "COMPLETED"
                        job.phase = "DONE"
                        db.save_job(job.to_dict())

                except asyncio.CancelledError:
                    if self.cancel_flags.get(job_id, False) and job_id in self.cancel_reasons:
                        reason = self.cancel_reasons[job_id]
                        print(f"[APPX {job_id}] CANCEL REQUESTED: {reason}")
                        logging.info(f"[APPX {job_id}] CANCEL REQUESTED: {reason}")
                    else:
                        print(f"[APPX {job_id}] UNEXPECTED TASK CANCELLATION source=Asyncio task cancellation during phase {job.phase}")
                        logging.warning(f"[APPX {job_id}] UNEXPECTED TASK CANCELLATION source=Asyncio task cancellation during phase {job.phase}")

                    job_data = db.get_job(job_id, bot_id=job.bot_id)
                    if job_data and job_data.get("status") not in ("CANCELLED", "PAUSED"):
                        job.status = "PAUSED"
                        job.phase = "PAUSED"
                        db.save_job(job.to_dict())
                except Exception as exc:
                    logging.exception(f"[APPX {job_id}] Execution error: {exc}")
                    job.status = "FAILED"
                    job.error_message = str(exc)
                    db.save_job(job.to_dict())
                    try:
                        await bot_client.send_message(
                            job.chat_id,
                            f"❌ <b>Job Failed:</b> <code>{job_id}</code>\n\n<blockquote>Reason: {str(exc)}</blockquote>"
                        )
                    except Exception:
                        pass
                finally:
                    k = self._user_key(job.user_id, job.bot_id)
                    if self.user_active_jobs.get(k) == job_id:
                        self.user_active_jobs.pop(k, None)
                    self.active_tasks.pop(job_id, None)
                    self.cancel_flags.pop(job_id, None)
                    self.cancel_reasons.pop(job_id, None)


# Default global job manager instance for backward compatibility
job_manager = JobManager("bot_1")
