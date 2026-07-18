"""
Daytona wiring PoC — Python SDK.

Proves: create sandbox -> write server file -> start server -> wait ready
-> get preview URL -> health check -> cleanup.

This is NOT the Provisioner. It exists only to validate the SDK calls
confirmed against the Daytona Python SDK docs (daytona-sdk on PyPI)
before the real Provisioner (PRD 4.1) is built on top of them.

Usage:
    python poc/daytona_smoke_test.py run [--port 8000] [--keep] [--timeout 90]
    python poc/daytona_smoke_test.py cleanup --sandbox-id <id>

Env (.env in repo root):
    DAYTONA_API_KEY   required
    DAYTONA_API_URL   optional, defaults to Daytona's hosted API
    DAYTONA_TARGET    optional (e.g. "us", "eu")
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daytona-poc")

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_FILE = Path(__file__).resolve().parent / "server.py"
REMOTE_SERVER_PATH = "server.py"
SESSION_ID = "poc-server"
API_CALL_TIMEOUT = 30  # seconds, per Daytona API call


def load_config():
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("DAYTONA_API_KEY")
    if not api_key:
        log.error("DAYTONA_API_KEY is not set (checked %s)", REPO_ROOT / ".env")
        sys.exit(1)
    return {
        "api_key": api_key,
        "api_url": os.environ.get("DAYTONA_API_URL") or os.environ.get("DAYTONA_BASE_URL"),
        "target": os.environ.get("DAYTONA_TARGET"),
    }


def make_client():
    from daytona import Daytona, DaytonaConfig

    cfg = load_config()
    kwargs = {"api_key": cfg["api_key"]}
    if cfg["api_url"]:
        kwargs["api_url"] = cfg["api_url"]
    if cfg["target"]:
        kwargs["target"] = cfg["target"]
    log.info("STEP 0/6: connecting to Daytona (api_url=%s)", cfg["api_url"] or "default")
    return Daytona(DaytonaConfig(**kwargs))


def create_sandbox(daytona):
    log.info("STEP 1/6: creating sandbox")
    sandbox = daytona.create()
    log.info("STEP 1/6: sandbox created id=%s", sandbox.id)
    return sandbox


def write_server_file(sandbox):
    log.info("STEP 2/6: writing %s into sandbox as %s", SERVER_FILE.name, REMOTE_SERVER_PATH)
    content = SERVER_FILE.read_bytes()
    sandbox.fs.upload_file(content, REMOTE_SERVER_PATH)
    log.info("STEP 2/6: file written (%d bytes)", len(content))


def start_server(sandbox, port):
    log.info("STEP 3/6: starting server on port %d", port)
    from daytona import SessionExecuteRequest

    sandbox.process.create_session(SESSION_ID)
    cmd = sandbox.process.execute_session_command(
        SESSION_ID,
        SessionExecuteRequest(
            command=f"python3 {REMOTE_SERVER_PATH} {port}",
            run_async=True,
        ),
    )
    log.info("STEP 3/6: server process started (cmd_id=%s)", getattr(cmd, "cmd_id", cmd))
    return cmd


def wait_until_ready(sandbox, port, timeout_s):
    log.info("STEP 4/6: waiting for server readiness (timeout=%ss)", timeout_s)
    deadline = time.monotonic() + timeout_s
    last_err = None
    while time.monotonic() < deadline:
        try:
            preview = sandbox.get_preview_link(port)
            resp = _get(preview, "/health")
            if resp.status_code == 200:
                log.info("STEP 4/6: server ready")
                return preview
        except Exception as exc:  # connection refused, not-yet-listening, etc.
            last_err = exc
        time.sleep(2)
    raise TimeoutError(f"server did not become ready within {timeout_s}s (last error: {last_err})")


def get_preview_url(sandbox, port):
    log.info("STEP 5/6: retrieving preview URL for port %d", port)
    preview = sandbox.get_preview_link(port)
    log.info("STEP 5/6: preview URL = %s", preview.url)
    return preview


def _get(preview, path):
    import requests

    headers = {"x-daytona-preview-token": preview.token} if getattr(preview, "token", None) else {}
    return requests.get(preview.url.rstrip("/") + path, headers=headers, timeout=API_CALL_TIMEOUT)


def health_check(preview):
    log.info("STEP 6/6: calling /health")
    resp = _get(preview, "/health")
    if resp.status_code != 200:
        raise RuntimeError(f"health check failed: HTTP {resp.status_code} {resp.text[:200]}")
    log.info("STEP 6/6: health check OK (%s)", resp.text.strip())


def cleanup(daytona, sandbox_id):
    log.info("CLEANUP: deleting sandbox %s", sandbox_id)
    sandbox = daytona.get(sandbox_id)
    sandbox.delete(timeout=60, wait=True)
    log.info("CLEANUP: sandbox %s deleted", sandbox_id)


def run(args):
    daytona = make_client()
    sandbox = None
    try:
        sandbox = create_sandbox(daytona)
        write_server_file(sandbox)
        start_server(sandbox, args.port)
        preview = wait_until_ready(sandbox, args.port, args.timeout)
        health_check(preview)
        log.info("RESULT: PASS — sandbox=%s url=%s", sandbox.id, preview.url)
    except Exception:
        log.exception("PoC failed")
        if sandbox is not None and not args.keep:
            log.info("CLEANUP: deleting sandbox %s after failure", sandbox.id)
            sandbox.delete(timeout=60, wait=True)
        sys.exit(1)

    if args.keep:
        log.info("KEEP: sandbox %s left running (clean up later with: "
                  "python poc/daytona_smoke_test.py cleanup --sandbox-id %s)", sandbox.id, sandbox.id)
    else:
        log.info("CLEANUP: deleting sandbox %s", sandbox.id)
        sandbox.delete(timeout=60, wait=True)
        log.info("CLEANUP: done")


def cleanup_cmd(args):
    daytona = make_client()
    cleanup(daytona, args.sandbox_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the full create->write->start->health-check flow")
    p_run.add_argument("--port", type=int, default=8000)
    p_run.add_argument("--timeout", type=int, default=90, help="readiness timeout in seconds")
    p_run.add_argument("--keep", action="store_true", help="do not delete the sandbox on success")
    p_run.set_defaults(func=run)

    p_cleanup = sub.add_parser("cleanup", help="delete a sandbox left running by a previous --keep run")
    p_cleanup.add_argument("--sandbox-id", required=True)
    p_cleanup.set_defaults(func=cleanup_cmd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
