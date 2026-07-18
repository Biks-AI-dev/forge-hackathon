import logging
import shutil
from pathlib import Path

from . import config
from .file_validation import safe_temp_filename

log = logging.getLogger("meeting-mode")


def job_dir(job_id: str) -> Path:
    """All files for one processing job live under one directory, outside
    any public/static path — config.JOBS_DIR is never mounted as static."""
    d = config.JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(job_id: str, original_filename: str, data: bytes) -> Path:
    dest = job_dir(job_id) / safe_temp_filename(original_filename)
    dest.write_bytes(data)
    return dest


def cleanup_job(job_id: str, *, reason: str) -> None:
    d = config.JOBS_DIR / job_id
    if not d.exists():
        return
    try:
        shutil.rmtree(d)
        log.info("job=%s cleanup ok reason=%s", job_id, reason)
    except Exception as exc:
        log.error("job=%s cleanup FAILED reason=%s error=%s", job_id, reason, exc)
