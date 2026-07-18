import json
import logging
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger("provisioner")

DEFAULT_MANIFEST = {
    "start_command": "python3 server.py",
    "install_command": None,
    "port": 8000,
    "health_path": "/health",
    "health_timeout_s": 60,
    "install_timeout_s": 120,
    "env_passthrough": [],
}


class AgentManifest(BaseModel):
    start_command: str
    install_command: str | None = None
    port: int
    health_path: str
    health_timeout_s: int = 60
    install_timeout_s: int = 120
    env_passthrough: list[str] = []


def load_manifest(template_dir: Path) -> AgentManifest:
    """Reads agent-template/forge.manifest.json — Dev B's declared runtime
    contract (start command, port, health path, which secrets it needs).
    Falls back to documented defaults if Dev B hasn't added the file yet,
    so a missing manifest degrades gracefully instead of blocking forges.
    """
    manifest_path = template_dir / "forge.manifest.json"
    data = dict(DEFAULT_MANIFEST)
    if manifest_path.exists():
        try:
            data.update(json.loads(manifest_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ValueError(f"agent-template/forge.manifest.json is not valid JSON: {exc}") from exc
    else:
        log.warning(
            "no forge.manifest.json in %s, using defaults: %s", template_dir, DEFAULT_MANIFEST
        )
    return AgentManifest.model_validate(data)
