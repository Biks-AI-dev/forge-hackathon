"""Orchestrates one job: Transcribe -> Extract + generate -> Validate ->
Provisioner (PRD §5). No confirmation screen anywhere in this chain — a
validated spec is posted to /forge automatically; an invalid one fails the
job and tells the user what to fix on "Record again"."""
import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from . import config, extraction, llm, storage, transcription
from .forgespec_validation import validate_generated_spec
from .job_store import Job, JobState, store

log = logging.getLogger("meeting-mode")


@dataclass
class ClientFile:
    path: Path
    original_filename: str
    kind: str  # "pdf" | "excel"


def _resolve_business_name(spec_dict: dict) -> Optional[str]:
    if spec_dict.get("business_name"):
        return spec_dict["business_name"]
    if (spec_dict.get("business") or {}).get("name"):
        return spec_dict["business"]["name"]
    if ((spec_dict.get("products") or {}).get("store") or {}).get("name"):
        return spec_dict["products"]["store"]["name"]
    return None


def _force_business_identity(spec_dict: dict, business_name: str) -> None:
    """Used only on 'Record again' continuations (PRD §5 step 11): rather
    than hope the LLM names the business identically twice, the prior
    resolved name is stamped onto the new spec so the slug — and
    therefore the Provisioner's replace-not-duplicate behavior — is
    guaranteed, not just likely."""
    if spec_dict.get("business_name") is not None:
        spec_dict["business_name"] = business_name
    if spec_dict.get("business") is not None:
        spec_dict["business"]["name"] = business_name
    elif ((spec_dict.get("products") or {}).get("store")) is not None:
        spec_dict["products"]["store"]["name"] = business_name


async def run_pipeline(
    job_id: str,
    *,
    audio_path: Path,
    client_files: list[ClientFile],
    research_context: Optional[str],
    continuation_business_name: Optional[str],
    continuation_workflow: Optional[str] = None,
) -> None:
    try:
        await _run(job_id, audio_path, client_files, research_context, continuation_business_name, continuation_workflow)
    except BaseException as exc:
        # Backstop, not the primary error path (each stage already has its
        # own try/except -> store.fail()). Caught live in a Gate 2
        # rehearsal: asyncio.wait_for() racing a to_thread() call whose
        # underlying thread can't actually be cancelled can leak a bare
        # CancelledError past a normal `except Exception` (CancelledError
        # is a BaseException, not an Exception, since Python 3.8). Without
        # this, the job froze forever in its last state — temp files
        # already deleted by the `finally` below, no error ever shown to
        # the user, no way to "Record again" out of it. Any job must end
        # in a terminal state; this guarantees it even for exception types
        # the per-stage handlers don't anticipate.
        job = store.get(job_id)
        if job and job.state not in (JobState.READY, JobState.FAILED):
            log.error("job=%s pipeline crashed with unhandled %s: %s", job_id, type(exc).__name__, exc)
            store.fail(job_id, "pipeline_crashed", f"unexpected pipeline failure ({type(exc).__name__})")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        storage.cleanup_job(job_id, reason="pipeline_complete")


async def _run(
    job_id: str,
    audio_path: Path,
    client_files: list[ClientFile],
    research_context: Optional[str],
    continuation_business_name: Optional[str],
    continuation_workflow: Optional[str] = None,
) -> None:
    job = store.get(job_id)
    if job is None:
        return

    # PRD requirement 10: log whether enrichment was requested/included,
    # without logging its content (it's already logged, source-attributed,
    # by research.py's own run_research() at request time — this just
    # records whether THIS job actually used it).
    log.info("job=%s pre-meeting research included=%s", job_id, bool(research_context))

    # --- Transcribe ---
    store.set_state(job_id, JobState.TRANSCRIBING)
    try:
        result = await asyncio.to_thread(transcription.transcribe, audio_path)
    except transcription.TranscriptionError as exc:
        store.fail(job_id, "transcription_failed", str(exc))
        return
    except Exception as exc:
        log.exception("job=%s unexpected transcription error", job_id)
        store.fail(job_id, "transcription_failed", f"unexpected error: {exc}")
        return

    job.transcript_provider = result.provider
    job.transcript_duration_s = result.duration_s
    # Shown back to the user in their own UI (their own meeting, their own
    # data) — not a server log, so this is fine even though pipeline.py
    # elsewhere deliberately never logs transcript content.
    job.transcript_preview = result.text[:600]
    log.info("job=%s transcription ok provider=%s chars=%d", job_id, result.provider, len(result.text))

    # --- Read client files ---
    store.set_state(job_id, JobState.READING_FILES)
    excel_summaries: list[str] = []
    pdf_summaries: list[str] = []
    for cf in client_files:
        try:
            if cf.kind == "excel":
                extracted = await asyncio.to_thread(extraction.extract_excel, cf.path, cf.original_filename)
                excel_summaries.append(extraction.summarize_excel(extracted))
            else:
                extracted = await asyncio.to_thread(extraction.extract_pdf, cf.path, cf.original_filename)
                pdf_summaries.append(extraction.summarize_pdf(extracted))
        except extraction.ExtractionError as exc:
            store.fail(job_id, "file_extraction_failed", str(exc))
            return
        except Exception as exc:
            log.exception("job=%s unexpected extraction error file=%s", job_id, cf.original_filename)
            store.fail(job_id, "file_extraction_failed", f"{cf.original_filename}: unexpected error: {exc}")
            return
    log.info("job=%s extraction ok excel=%d pdf=%d", job_id, len(excel_summaries), len(pdf_summaries))

    # --- Extract + generate (LLM) + validate, with one retry of the pair ---
    # Gate 2 rehearsal measured ~50% first-try clean-success rate across
    # generation AND validation combined (mix of transient timeouts and
    # legitimate validation catches — LLM sampling variance, not a
    # systematic bug). Retrying the pair, not just generation alone, means
    # a validation catch (e.g. empty guardrails) also gets a second shot
    # instead of failing the job outright. A single retry trades a few
    # extra seconds for meaningfully better odds on a one-take stage demo,
    # without masking a genuinely bad input: if attempt 2 also fails, the
    # user sees that attempt's specific error, not a generic "gave up".
    max_attempts = 2
    spec = None
    raw_spec = None
    last_error: tuple[str, str, list] = ("forgespec_generation_failed", "no attempt completed", [])

    for attempt in range(1, max_attempts + 1):
        will_retry = attempt < max_attempts
        store.set_state(job_id, JobState.EXTRACTING_FORGESPEC)
        try:
            # requests' `timeout=` only bounds the gap *between* reads, not
            # the total call — a slow trickle of bytes can outlive it
            # indefinitely (observed live: a job hung 2+ minutes past
            # LLM_TIMEOUT_S=60 with no error). This wait_for is the real
            # upper bound the job-facing user sees; it can't kill the
            # underlying thread (blocking requests calls aren't
            # cancellable), so a hung call still burns a worker thread in
            # the background — acceptable for a hackathon single-process
            # demo, not for production.
            attempt_spec = await asyncio.wait_for(
                asyncio.to_thread(
                    llm.generate_forge_spec,
                    transcript=result.text,
                    excel_summaries=excel_summaries,
                    pdf_summaries=pdf_summaries,
                    research_context=research_context,
                    workflow_hint=continuation_workflow,
                ),
                timeout=config.LLM_TIMEOUT_S + 15,
            )
        except asyncio.TimeoutError:
            last_error = ("forgespec_generation_failed", f"LLM call exceeded {config.LLM_TIMEOUT_S + 15:.0f}s", [])
            log.warning("job=%s attempt %d/%d: LLM timeout, %s", job_id, attempt, max_attempts,
                        "retrying" if will_retry else "giving up")
            continue
        except llm.LLMError as exc:
            last_error = ("forgespec_generation_failed", str(exc), [])
            log.warning("job=%s attempt %d/%d: LLM error (%s), %s", job_id, attempt, max_attempts, exc,
                        "retrying" if will_retry else "giving up")
            continue
        except Exception as exc:
            log.exception("job=%s unexpected LLM error", job_id)
            last_error = ("forgespec_generation_failed", f"unexpected error: {exc}", [])
            continue

        if continuation_workflow and attempt_spec.get("workflow") != continuation_workflow:
            last_error = (
                "workflow_changed_on_continuation",
                f"this recording was classified as '{attempt_spec.get('workflow')}' but the previous forge "
                f"for this business was '{continuation_workflow}'. Record again and keep the same kind of "
                f"business problem described, or start a new (non-'Record again') session if the business "
                f"has genuinely changed.",
                [],
            )
            log.warning("job=%s attempt %d/%d: workflow drifted, %s", job_id, attempt, max_attempts,
                        "retrying" if will_retry else "giving up")
            continue

        if continuation_business_name:
            _force_business_identity(attempt_spec, continuation_business_name)

        store.set_state(job_id, JobState.VALIDATING_FORGESPEC)
        try:
            attempt_validated = validate_generated_spec(attempt_spec)
        except Exception as exc:
            details = [d.model_dump() for d in getattr(exc, "details", [])]
            last_error = ("validation_failed", "generated ForgeSpec failed validation", details)
            log.warning("job=%s attempt %d/%d: validation failed (%s), %s", job_id, attempt, max_attempts,
                        details, "retrying" if will_retry else "giving up")
            continue

        raw_spec, spec = attempt_spec, attempt_validated
        break

    if spec is None:
        store.fail(job_id, last_error[0], last_error[1], last_error[2])
        return

    if continuation_business_name:
        log.info("job=%s record-again: business identity pinned to prior job's resolved name", job_id)

    job.resolved_business_name = _resolve_business_name(raw_spec)
    job.resolved_workflow = raw_spec.get("workflow")
    log.info("job=%s forgespec generated workflow=%s", job_id, raw_spec.get("workflow"))

    conflict_notes = raw_spec.get("notes") or []
    if conflict_notes:
        # PRD requirement 10: log any source conflict detected. These are
        # conflict *descriptions* (e.g. "transcript said X, file said Y"),
        # not raw transcript/file content, so logging them in full is safe
        # and useful for ops — unlike the transcript/file text itself.
        for note in conflict_notes:
            log.info("job=%s source conflict: %s", job_id, note)

    # --- Provision ---
    store.set_state(job_id, JobState.PROVISIONING)
    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                requests.post,
                f"{config.PROVISIONER_URL.rstrip('/')}/forge",
                json=spec.model_dump(mode="json"),
                timeout=config.PROVISION_TIMEOUT_S,
            ),
            timeout=config.PROVISION_TIMEOUT_S + 15,
        )
    except asyncio.TimeoutError:
        store.fail(job_id, "provisioning_failed", f"Provisioner call exceeded {config.PROVISION_TIMEOUT_S + 15:.0f}s")
        return
    except requests.RequestException as exc:
        store.fail(job_id, "provisioning_failed", f"could not reach Provisioner: {exc}")
        return

    if resp.status_code != 200:
        store.fail(job_id, "provisioning_failed", f"Provisioner returned HTTP {resp.status_code}: {resp.text[:300]}")
        return

    body = resp.json()
    job.chat_url = body.get("chat_url")
    job.sandbox_id = body.get("sandbox_id")
    job.slug = body.get("slug")
    job.elapsed_ms = body.get("elapsed_ms")
    job.replaced_sandbox_id = body.get("replaced_sandbox_id")

    store.set_state(job_id, JobState.READY)
    log.info(
        "job=%s READY slug=%s sandbox_id=%s total_pipeline_s=%.1f",
        job_id, job.slug, job.sandbox_id, time.monotonic() - t0,
    )
