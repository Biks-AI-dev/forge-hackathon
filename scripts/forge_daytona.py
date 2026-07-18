"""
THE HANDOVER provisioner: clones the AI employee into the client's own
isolated Daytona sandbox.

Usage: python forge_daytona.py /path/to/agentDir
  agentDir must contain spec.json. The sandbox id is cached in
  agentDir/.sandbox_id, so the same client re-forges into the SAME sandbox
  (refine -> handover -> refine keeps one link alive).

Prints {"url": ..., "sandboxId": ..., "public": ...} as the last stdout line.

The sandbox is created public when the SDK supports it (permanent URL, no
signature expiry); otherwise falls back to a 24h signed preview URL.
Note: Daytona sandboxes currently block outbound HTTPS, so the agent runs
template-only in there (no Kimi voice) — by design it stays fully functional:
every decision is code, the voice step is optional garnish.
"""

import json
import sys
import time
from pathlib import Path

from daytona import Daytona, CreateSandboxFromSnapshotParams, CodeLanguage, SessionExecuteRequest

AGENT_PORT = 8000
SESSION_ID = "employee"
TEMPLATE = Path(__file__).resolve().parent.parent / "agent-template" / "server.py"


def get_or_create(daytona, cache: Path):
    if cache.exists():
        sid = cache.read_text().strip()
        try:
            sb = daytona.get(sid)
            if not str(getattr(sb, "state", "")).lower().endswith("started"):
                sb.start()
            return sb, False
        except Exception:
            pass  # stale id -> create fresh
    public = True
    try:
        sb = daytona.create(CreateSandboxFromSnapshotParams(
            language=CodeLanguage.PYTHON, auto_stop_interval=0, public=True))
    except TypeError:
        public = False
        sb = daytona.create(CreateSandboxFromSnapshotParams(
            language=CodeLanguage.PYTHON, auto_stop_interval=0))
    cache.write_text(sb.id)
    sb._forge_public = public
    return sb, True


def main():
    agent_dir = Path(sys.argv[1])
    spec_path = agent_dir / "spec.json"
    if not spec_path.exists():
        raise SystemExit(f"spec.json not found in {agent_dir}")

    daytona = Daytona()
    sb, created = get_or_create(daytona, agent_dir / ".sandbox_id")
    public = getattr(sb, "_forge_public", None)
    if public is None:
        public = bool(getattr(getattr(sb, "instance", sb), "public", False))

    home = sb.get_user_home_dir()
    app = f"{home}/employee"
    sb.fs.upload_file(str(TEMPLATE), f"{app}/server.py")
    sb.fs.upload_file(str(spec_path), f"{app}/spec.json")

    if created:
        sb.process.create_session(SESSION_ID)
    # (re)start so a re-handover always runs the freshest spec
    sb.process.execute_session_command(SESSION_ID, SessionExecuteRequest(
        command=(f"pkill -f server.py >/dev/null 2>&1; sleep 1; "
                 f"cd {app} && PORT={AGENT_PORT} nohup python3 server.py > employee.log 2>&1 &"),
        run_async=True))
    time.sleep(3)

    if public:
        url = sb.get_preview_link(AGENT_PORT).url
    else:
        url = sb.create_signed_preview_url(AGENT_PORT, expires_in_seconds=24 * 3600 - 60).url

    print(json.dumps({"url": url, "sandboxId": sb.id, "public": public}))


if __name__ == "__main__":
    main()
