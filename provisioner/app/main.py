import asyncio
import json
import logging
import os
import time

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config, daytona_ops
from .errors import ProvisionError, ProvisionTimeoutError
from .manifest import load_manifest
from .models import ForgeErrorResponse, ForgeResponse, ValidationErrorResponse
from .registry import registry, spec_hash
from .slug import slugify
from .validation import ForgeSpecValidationError, validate_forge_spec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("provisioner")

app = FastAPI(title="Biks Forge Provisioner")


@app.exception_handler(ForgeSpecValidationError)
async def handle_validation_error(request: Request, exc: ForgeSpecValidationError):
    return JSONResponse(
        status_code=400,
        content=ValidationErrorResponse(
            message="ForgeSpec failed validation", details=exc.details
        ).model_dump(),
    )


@app.exception_handler(ProvisionError)
async def handle_provision_error(request: Request, exc: ProvisionError):
    log.error("[%s] %s (sandbox_id=%s)", exc.error_code, exc.message, exc.sandbox_id)
    return JSONResponse(
        status_code=exc.status_code,
        content=ForgeErrorResponse(error=exc.error_code, message=exc.message).model_dump(),
    )


def _step(label: str, t0: float) -> float:
    now = time.monotonic()
    log.info("step=%s elapsed_ms=%d", label, int((now - t0) * 1000))
    return now


def _provision_sync(spec_dict: dict, spec_h: str, slug: str, request_t0: float) -> tuple[str, str]:
    """Blocking pipeline: create -> inject -> start -> health -> preview.
    Runs on a worker thread. Returns (sandbox_id, chat_url). Cleans up the
    new sandbox itself on any failure so the caller never has to.

    Known limitation: once dispatched to a thread, this cannot be
    cancelled mid-step by the asyncio timeout wrapper (SDK calls are
    synchronous/blocking). The deadline checks below make it fail fast
    *between* steps instead of relying on cancellation.
    """
    deadline = request_t0 + config.PROVISION_TIMEOUT_S
    daytona = daytona_ops.make_client()
    manifest = load_manifest(config.AGENT_TEMPLATE_DIR)

    sandbox = daytona_ops.create_sandbox(daytona)
    t = _step("create_sandbox", request_t0)

    try:
        if time.monotonic() > deadline:
            raise ProvisionTimeoutError("deadline exceeded before injection", sandbox.id)
        daytona_ops.inject_agent_template(sandbox, config.AGENT_TEMPLATE_DIR)
        t = _step("inject_agent_template", t)

        daytona_ops.write_spec_json(sandbox, spec_dict_to_bytes(spec_dict))
        t = _step("write_spec_json", t)

        secret_env = {
            name: os.environ[name] for name in manifest.env_passthrough if name in os.environ
        }
        missing = [n for n in manifest.env_passthrough if n not in secret_env]
        if missing:
            log.warning("env_passthrough names not set in Provisioner env, skipped: %s", missing)
        secret_env["PORT"] = str(manifest.port)
        daytona_ops.set_runtime_env(sandbox, secret_env)
        t = _step("set_runtime_env", t)

        if time.monotonic() > deadline:
            raise ProvisionTimeoutError("deadline exceeded before install/start", sandbox.id)
        daytona_ops.maybe_install(sandbox, manifest)
        t = _step("maybe_install", t)

        daytona_ops.start_server(sandbox, manifest)
        t = _step("start_server", t)

        daytona_ops.wait_until_healthy(sandbox, manifest)
        t = _step("wait_until_healthy", t)

        chat_url = daytona_ops.get_preview_url(sandbox, manifest.port)
        _step("get_preview_url", t)

    except Exception:
        log.error("provisioning failed, cleaning up new sandbox %s", sandbox.id)
        daytona_ops.delete_sandbox(sandbox, context="rollback-new-sandbox")
        raise

    return sandbox.id, chat_url


def spec_dict_to_bytes(spec_dict: dict) -> bytes:
    return json.dumps(spec_dict, indent=2).encode("utf-8")


def _quick_health_probe(chat_url: str, health_path: str) -> bool:
    try:
        resp = requests.get(chat_url.rstrip("/") + health_path, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


@app.post("/forge")
async def forge(request: Request):
    request_t0 = time.monotonic()
    try:
        raw = await request.json()
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content=ValidationErrorResponse(
                message="request body is not valid JSON",
                details=[{"field": "(body)", "message": str(exc)}],
            ).model_dump(),
        )
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content=ValidationErrorResponse(
                message="request body must be a JSON object",
                details=[{"field": "(body)", "message": f"got {type(raw).__name__}"}],
            ).model_dump(),
        )

    spec = validate_forge_spec(raw)
    spec_dict = spec.model_dump(mode="json")
    # Workflow is part of the identity: the same business can now forge one
    # agent per workflow (Meeting Mode's multi-topic pipeline), and those
    # must coexist, not replace each other. Specs without a workflow keep
    # the plain business-name slug (legacy behavior).
    slug_source = spec.resolved_business_name()
    if spec.workflow:
        slug_source = f"{slug_source} {spec.workflow}"
    slug = slugify(slug_source)
    h = spec_hash(spec_dict)

    lock = await registry.lock_for(slug)
    async with lock:
        existing = registry.get(slug)

        # Idempotent fast path (PRD "Record again" semantics: unchanged
        # spec + healthy sandbox => no-op, not a fresh replace).
        if existing and existing.spec_hash == h:
            manifest = load_manifest(config.AGENT_TEMPLATE_DIR)
            healthy = await asyncio.to_thread(_quick_health_probe, existing.chat_url, manifest.health_path)
            if healthy:
                elapsed_ms = int((time.monotonic() - request_t0) * 1000)
                log.info("idempotent hit for slug=%s, skipping re-provision", slug)
                return ForgeResponse(
                    chat_url=existing.chat_url,
                    sandbox_id=existing.sandbox_id,
                    slug=slug,
                    elapsed_ms=elapsed_ms,
                    replaced_sandbox_id=None,
                )

        try:
            sandbox_id, chat_url = await asyncio.wait_for(
                asyncio.to_thread(_provision_sync, spec_dict, h, slug, request_t0),
                timeout=config.PROVISION_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            raise ProvisionTimeoutError(
                f"provisioning exceeded {config.PROVISION_TIMEOUT_S}s"
            ) from exc

        replaced_sandbox_id = None
        if existing and existing.sandbox_id != sandbox_id:
            # New sandbox is confirmed healthy at this point — only now is
            # it safe to tear down the old one. Best-effort: a failed
            # teardown here does not fail the request (the new sandbox is
            # already live and correct), it just needs manual cleanup.
            daytona = daytona_ops.make_client()
            daytona_ops.delete_sandbox_by_id(daytona, existing.sandbox_id, context="replace-old-sandbox")
            replaced_sandbox_id = existing.sandbox_id

        registry.put(slug, sandbox_id, chat_url, h)

        elapsed_ms = int((time.monotonic() - request_t0) * 1000)
        log.info("forge complete slug=%s sandbox_id=%s elapsed_ms=%d", slug, sandbox_id, elapsed_ms)

        return ForgeResponse(
            chat_url=chat_url,
            sandbox_id=sandbox_id,
            slug=slug,
            elapsed_ms=elapsed_ms,
            replaced_sandbox_id=replaced_sandbox_id,
        )


@app.get("/health")
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
