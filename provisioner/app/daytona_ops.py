"""
Sandbox lifecycle, built on the Daytona Python SDK calls confirmed and
smoke-tested in poc/daytona_smoke_test.py. Synchronous by design (matches
the SDK) — the FastAPI layer runs this on a worker thread.

Dev A owns this file (copy / inject / start / replace / expose). It must
never inspect or change agent behavior — only the manifest contract in
agent-template/forge.manifest.json.
"""
import logging
import time
from pathlib import Path

import requests
from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    Sandbox,
    SessionExecuteRequest,
)

from . import config
from .errors import (
    HealthCheckTimeoutError,
    InjectionError,
    PreviewUrlError,
    SandboxCreateError,
    StartError,
)
from .manifest import AgentManifest

log = logging.getLogger("provisioner")

SESSION_ID = "agent"
API_CALL_TIMEOUT_S = 30


def make_client() -> Daytona:
    api_key = config.require_daytona_key()
    kwargs = {"api_key": api_key}
    if config.DAYTONA_API_URL:
        kwargs["api_url"] = config.DAYTONA_API_URL
    if config.DAYTONA_TARGET:
        kwargs["target"] = config.DAYTONA_TARGET
    return Daytona(DaytonaConfig(**kwargs))


def create_sandbox(daytona: Daytona) -> Sandbox:
    try:
        # public=True: the chat_url goes to the prospect, who opens it cold
        # in a plain browser tab with no way to attach a preview auth token.
        # Default (private) sandboxes redirect unauthenticated requests to
        # an Auth0 login page — confirmed against a live sandbox.
        sandbox = daytona.create(CreateSandboxFromSnapshotParams(public=True))
    except Exception as exc:
        raise SandboxCreateError(f"sandbox creation failed: {exc}") from exc
    log.info("sandbox created id=%s (public)", sandbox.id)
    return sandbox


def inject_agent_template(sandbox: Sandbox, template_dir: Path) -> int:
    """Copies every file under agent-template/ into the sandbox, preserving
    relative paths, except Provisioner-only metadata (manifest, README)."""
    if not template_dir.is_dir():
        raise InjectionError(f"agent-template not found at {template_dir}", sandbox.id)

    count = 0
    try:
        for path in sorted(template_dir.rglob("*")):
            if path.is_dir():
                continue
            if path.name in config.TEMPLATE_EXCLUDE_NAMES:
                continue
            if any(part in config.TEMPLATE_EXCLUDE_NAMES for part in path.relative_to(template_dir).parts):
                continue
            remote_path = str(path.relative_to(template_dir))
            sandbox.fs.upload_file(path.read_bytes(), remote_path)
            count += 1
    except InjectionError:
        raise
    except Exception as exc:
        raise InjectionError(f"failed injecting agent-template: {exc}", sandbox.id) from exc

    if count == 0:
        raise InjectionError(f"agent-template at {template_dir} has no files to inject", sandbox.id)

    log.info("injected %d agent-template file(s)", count)
    return count


def write_spec_json(sandbox: Sandbox, spec_json_bytes: bytes) -> None:
    try:
        sandbox.fs.upload_file(spec_json_bytes, "spec.json")
    except Exception as exc:
        raise InjectionError(f"failed writing spec.json: {exc}", sandbox.id) from exc
    log.info("spec.json written (%d bytes)", len(spec_json_bytes))


def set_runtime_env(sandbox: Sandbox, env: dict[str, str]) -> None:
    """Secrets and runtime config go here, never into a file. Only key
    names are logged, never values."""
    if not env:
        return
    try:
        sandbox.update_env(env)
    except Exception as exc:
        raise InjectionError(f"failed setting runtime env: {exc}", sandbox.id) from exc
    log.info("runtime env set for keys: %s", sorted(env.keys()))


def maybe_install(sandbox: Sandbox, manifest: AgentManifest) -> None:
    if not manifest.install_command:
        return
    log.info("running install command")
    try:
        result = sandbox.process.exec(manifest.install_command, timeout=manifest.install_timeout_s)
    except Exception as exc:
        raise StartError(f"install command failed to run: {exc}", sandbox.id) from exc
    if result.exit_code != 0:
        raise StartError(
            f"install command exited {result.exit_code}: {(result.result or '')[-500:]}", sandbox.id
        )
    log.info("install command completed exit_code=0")


def start_server(sandbox: Sandbox, manifest: AgentManifest) -> None:
    try:
        sandbox.process.create_session(SESSION_ID)
        sandbox.process.execute_session_command(
            SESSION_ID,
            SessionExecuteRequest(command=manifest.start_command, run_async=True),
        )
    except Exception as exc:
        raise StartError(f"failed to start agent server: {exc}", sandbox.id) from exc
    log.info("agent server start command dispatched")


def _preview_health_ok(sandbox: Sandbox, port: int, health_path: str) -> bool:
    try:
        preview = sandbox.get_preview_link(port)
        headers = {"x-daytona-preview-token": preview.token} if getattr(preview, "token", None) else {}
        resp = requests.get(
            preview.url.rstrip("/") + health_path, headers=headers, timeout=API_CALL_TIMEOUT_S
        )
        return resp.status_code == 200
    except Exception:
        return False


def wait_until_healthy(sandbox: Sandbox, manifest: AgentManifest) -> None:
    deadline = time.monotonic() + manifest.health_timeout_s
    while time.monotonic() < deadline:
        if _preview_health_ok(sandbox, manifest.port, manifest.health_path):
            log.info("health check passed")
            return
        time.sleep(2)
    raise HealthCheckTimeoutError(
        f"server not healthy within {manifest.health_timeout_s}s on {manifest.health_path}", sandbox.id
    )


def get_preview_url(sandbox: Sandbox, port: int) -> str:
    try:
        preview = sandbox.get_preview_link(port)
    except Exception as exc:
        raise PreviewUrlError(f"failed to retrieve preview URL: {exc}", sandbox.id) from exc
    return preview.url


def delete_sandbox(sandbox: Sandbox, *, context: str) -> None:
    """Best-effort teardown. Never raises — a failed cleanup must not fail
    the request or mask the original error; it's logged for manual cleanup."""
    try:
        sandbox.delete(timeout=60, wait=True)
        log.info("[%s] sandbox %s deleted", context, sandbox.id)
    except Exception as exc:
        log.error("[%s] failed to delete sandbox %s (needs manual cleanup): %s", context, sandbox.id, exc)


def delete_sandbox_by_id(daytona: Daytona, sandbox_id: str, *, context: str) -> None:
    try:
        sandbox = daytona.get(sandbox_id)
    except Exception as exc:
        log.error("[%s] could not resolve sandbox %s for cleanup: %s", context, sandbox_id, exc)
        return
    delete_sandbox(sandbox, context=context)
