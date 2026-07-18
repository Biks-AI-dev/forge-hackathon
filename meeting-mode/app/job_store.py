import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import config, storage

log = logging.getLogger("meeting-mode")


class JobState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    READING_FILES = "reading_files"
    EXTRACTING_FORGESPEC = "extracting_forgespec"
    VALIDATING_FORGESPEC = "validating_forgespec"
    PROVISIONING = "provisioning"
    READY = "ready"
    FAILED = "failed"


TERMINAL_STATES = {JobState.READY, JobState.FAILED}


@dataclass
class Job:
    job_id: str
    session_id: str
    state: JobState = JobState.UPLOADING
    error: Optional[dict] = None
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)

    # result fields, populated as the pipeline progresses
    transcript_provider: Optional[str] = None
    transcript_duration_s: Optional[float] = None
    transcript_preview: Optional[str] = None
    resolved_business_name: Optional[str] = None
    resolved_workflow: Optional[str] = None
    chat_url: Optional[str] = None
    sandbox_id: Optional[str] = None
    slug: Optional[str] = None
    elapsed_ms: Optional[int] = None
    replaced_sandbox_id: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "error": self.error,
            "transcript_provider": self.transcript_provider,
            "transcript_duration_s": self.transcript_duration_s,
            "transcript_preview": self.transcript_preview,
            "resolved_business_name": self.resolved_business_name,
            "resolved_workflow": self.resolved_workflow,
            "chat_url": self.chat_url,
            "sandbox_id": self.sandbox_id,
            "slug": self.slug,
            "elapsed_ms": self.elapsed_ms,
            "replaced_sandbox_id": self.replaced_sandbox_id,
        }


class JobStore:
    """In-memory job registry. Enforces at most one active (non-terminal)
    job per session_id — the browser-side guard against double submission
    is backed by this server-side one too."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._active_by_session: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, job_id: str, session_id: str) -> Job:
        async with self._lock:
            active = self._active_by_session.get(session_id)
            if active and self._jobs.get(active) and self._jobs[active].state not in TERMINAL_STATES:
                raise DuplicateSubmissionError(active)
            job = Job(job_id=job_id, session_id=session_id)
            self._jobs[job_id] = job
            self._active_by_session[session_id] = job_id
            return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def set_state(self, job_id: str, state: JobState) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.state = state
        job.updated_at = time.monotonic()
        log.info("job=%s state=%s", job_id, state.value)

    def fail(self, job_id: str, error_code: str, message: str, details: list | None = None) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.state = JobState.FAILED
        job.error = {"error": error_code, "message": message, "details": details or []}
        job.updated_at = time.monotonic()
        log.error("job=%s FAILED error=%s message=%s", job_id, error_code, message)

    def sweep_abandoned(self) -> None:
        now = time.monotonic()
        stale = [
            jid for jid, j in self._jobs.items()
            if j.state not in TERMINAL_STATES and (now - j.updated_at) > config.JOB_TTL_S
        ]
        for jid in stale:
            job = self._jobs[jid]
            log.warning("job=%s abandoned (no progress for %.0fs), cleaning up", jid, now - job.updated_at)
            self.fail(jid, "abandoned_timeout", "job timed out with no progress")
            storage.cleanup_job(jid, reason="abandoned_timeout")
            self._active_by_session.pop(job.session_id, None)


class DuplicateSubmissionError(Exception):
    def __init__(self, existing_job_id: str):
        self.existing_job_id = existing_job_id
        super().__init__(f"a job is already active for this session: {existing_job_id}")


store = JobStore()


async def sweep_loop():
    while True:
        await asyncio.sleep(config.JOB_SWEEP_INTERVAL_S)
        try:
            store.sweep_abandoned()
        except Exception:
            log.exception("job sweep failed")
