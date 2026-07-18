"""
Deploys one HTML file into a Daytona sandbox and prints {"url": ..., "sandboxId": ...}
as the last stdout line. Reuses the sandbox across calls (id cached next to
the html in data/.sandbox_id); creates a fresh one if the old id is gone.

Usage: python daytona_deploy.py /path/to/prototype.html
"""

import json
import sys
from pathlib import Path

from daytona import Daytona, CreateSandboxFromSnapshotParams, CodeLanguage, SessionExecuteRequest

PREVIEW_PORT = 8000
PREVIEW_EXPIRES_SECONDS = 8 * 60 * 60
SESSION_ID = "notula-preview"


def get_or_create_sandbox(daytona, state_file: Path):
    if state_file.exists():
        sid = state_file.read_text().strip()
        try:
            sb = daytona.get(sid)
            if str(getattr(sb, "state", "")).lower().endswith("started"):
                return sb, False
            sb.start()
            return sb, False
        except Exception:
            pass  # stale id -> create new

    sb = daytona.create(CreateSandboxFromSnapshotParams(language=CodeLanguage.JAVASCRIPT, auto_stop_interval=60))
    state_file.write_text(sb.id)
    return sb, True


def main():
    html_path = Path(sys.argv[1])
    state_file = html_path.parent / ".sandbox_id"

    daytona = Daytona()
    sb, created = get_or_create_sandbox(daytona, state_file)

    home = sb.get_user_home_dir()
    sb.fs.upload_file(str(html_path), f"{home}/site/index.html")

    if created:
        sb.process.create_session(SESSION_ID)
        sb.process.execute_session_command(
            SESSION_ID,
            SessionExecuteRequest(command=f"cd {home}/site && python3 -m http.server {PREVIEW_PORT}", run_async=True),
        )

    signed = sb.create_signed_preview_url(PREVIEW_PORT, expires_in_seconds=PREVIEW_EXPIRES_SECONDS)
    print(json.dumps({"url": signed.url, "sandboxId": sb.id}))


if __name__ == "__main__":
    main()
