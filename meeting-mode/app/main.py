import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import file_validation, pipeline, research, storage
from .job_store import DuplicateSubmissionError, JobState, store, sweep_loop
from .research_store import store as research_store_instance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("meeting-mode")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(sweep_loop())
    yield
    task.cancel()


app = FastAPI(title="Biks Forge Meeting Mode", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


def _error(status: int, error: str, message: str, details: list | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "message": message, "details": details or []})


@app.post("/research")
async def create_research(
    company_name: str | None = Form(None),
    website: str | None = Form(None),
):
    """Pre-meeting enrichment (PRD requirement 1-5). Runs BEFORE a meeting,
    entirely outside the Gate 1/2 critical path — this endpoint is never
    called from within the /jobs pipeline. Oxylabs credentials never
    leave this process; the frontend only ever sees the normalized
    result (or a warning), never raw scraped HTML or the API key."""
    if not company_name and not website:
        return _error(400, "missing_input", "provide a company name or website (at least one required)")

    log.info("research requested company_name=%s website=%s", bool(company_name), bool(website))
    enrichment, warning = await asyncio.to_thread(research.run_research, company_name, website)

    research_id = uuid.uuid4().hex
    research_store_instance.put(research_id, enrichment, warning)

    if enrichment is None:
        return {"research_id": research_id, "status": "skipped", "warning": warning}

    return {
        "research_id": research_id,
        "status": "ok",
        "warning": warning,
        "summary": {
            "business_name": enrichment.business_name,
            "description": (enrichment.description or "")[:700],
            "source_urls": enrichment.source_urls,
        },
    }


@app.get("/research/{research_id}")
async def get_research(research_id: str):
    entry = research_store_instance.get(research_id)
    if entry is None:
        return _error(404, "research_not_found", f"no research result with id {research_id} (or it expired)")
    if entry.enrichment is None:
        return {"research_id": research_id, "status": "skipped", "warning": entry.warning}
    return {
        "research_id": research_id,
        "status": "ok",
        "warning": entry.warning,
        "summary": {
            "business_name": entry.enrichment.business_name,
            "description": (entry.enrichment.description or "")[:700],
            "source_urls": entry.enrichment.source_urls,
        },
    }


@app.post("/jobs")
async def create_job(
    request: Request,
    session_id: str = Form(...),
    audio: UploadFile | None = File(None),
    client_files: list[UploadFile] = File(default_factory=list),
    research_context: str | None = Form(None),
    research_id: str | None = Form(None),
    continuation_of: str | None = Form(None),
):
    # --- validate audio (required: recorded blob or upload fallback, same field either way) ---
    if audio is None or not audio.filename:
        return _error(400, "missing_audio", "meeting audio is required (recorded or uploaded)")
    audio_bytes = await audio.read()
    try:
        file_validation.validate_audio_upload(audio.filename or "audio", audio.content_type, audio_bytes)
    except file_validation.FileValidationError as exc:
        return _error(400, "invalid_audio", exc.message, [{"field": exc.field, "message": exc.message}])

    # --- validate client files: at least one supported file required ---
    if not client_files or all(not f.filename for f in client_files):
        return _error(400, "missing_client_files", "at least one PDF or Excel client file is required")

    validated_files: list[tuple[UploadFile, bytes, str]] = []
    details = []
    for f in client_files:
        data = await f.read()
        try:
            kind = file_validation.validate_client_file(f.filename or "", f.content_type, data)
            validated_files.append((f, data, kind))
        except file_validation.FileValidationError as exc:
            details.append({"field": exc.field, "message": exc.message})
    if details:
        return _error(400, "invalid_client_files", "one or more client files failed validation", details)
    if not validated_files:
        return _error(400, "missing_client_files", "at least one PDF or Excel client file is required")

    # --- resolve continuation ("Record again") business identity, best-effort ---
    # Pins both business name AND workflow. Business-name-only pinning was
    # tried first and passed its own test, but a live rehearsal (Gate 2)
    # caught the gap: a "corrected" recording for the same business (price
    # correction only) still flipped workflow sales -> recon, because the
    # correction's wording leaned on payment/bank language. Same slug, same
    # business, but the Provisioner would have forged a completely
    # different agent type. Pinning workflow too closes that.
    continuation_business_name = None
    continuation_workflow = None
    if continuation_of:
        prior = store.get(continuation_of)
        if prior and prior.state == JobState.READY and prior.resolved_business_name:
            continuation_business_name = prior.resolved_business_name
            continuation_workflow = prior.resolved_workflow
        else:
            log.warning(
                "continuation_of=%s could not be resolved safely (found=%s state=%s), "
                "proceeding without forcing business identity",
                continuation_of, bool(prior), getattr(prior, "state", None),
            )

    # --- resolve pre-meeting research (optional; PRD requirement 6: never
    # sent to the Provisioner directly, only ever as LLM context) ---
    combined_research_context = research_context
    if research_id:
        entry = research_store_instance.get(research_id)
        if entry and entry.enrichment:
            enrichment_text = entry.enrichment.to_prompt_text()
            combined_research_context = (
                f"{research_context}\n\n{enrichment_text}" if research_context else enrichment_text
            )
            log.info("job will use research_id=%s sources=%s", research_id, entry.enrichment.source_urls)
        elif entry:
            log.info("research_id=%s found but had no usable enrichment, proceeding without it", research_id)
        else:
            log.warning("research_id=%s not found or expired, proceeding without web enrichment", research_id)

    # --- create job (server-side duplicate-submission guard) ---
    job_id = uuid.uuid4().hex
    try:
        await store.create(job_id, session_id)
    except DuplicateSubmissionError as exc:
        return _error(409, "job_already_active", str(exc), [{"field": "session_id", "message": str(exc)}])

    # --- persist uploads to job-scoped temp storage, outside any public dir ---
    audio_path = storage.save_upload(job_id, audio.filename or "audio.webm", audio_bytes)
    saved_client_files = [
        pipeline.ClientFile(
            path=storage.save_upload(job_id, f.filename or "file", data),
            original_filename=f.filename or "file",
            kind=kind,
        )
        for (f, data, kind) in validated_files
    ]

    log.info(
        "job=%s created session=%s audio_bytes=%d client_files=%d continuation_of=%s",
        job_id, session_id, len(audio_bytes), len(saved_client_files), continuation_of,
    )

    asyncio.create_task(pipeline.run_pipeline(
        job_id,
        audio_path=audio_path,
        client_files=saved_client_files,
        research_context=combined_research_context,
        continuation_business_name=continuation_business_name,
        continuation_workflow=continuation_workflow,
    ))

    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        return _error(404, "job_not_found", f"no job with id {job_id}")
    return job.to_public_dict()
