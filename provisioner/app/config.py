import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

DAYTONA_API_KEY = os.environ.get("DAYTONA_API_KEY")
DAYTONA_API_URL = os.environ.get("DAYTONA_API_URL") or os.environ.get("DAYTONA_BASE_URL")
DAYTONA_TARGET = os.environ.get("DAYTONA_TARGET")

AGENT_TEMPLATE_DIR = Path(os.environ.get("AGENT_TEMPLATE_DIR", REPO_ROOT / "agent-template")).resolve()

# Hard ceiling for a single /forge request. PRD target is 90s to a reachable
# chat URL; this leaves headroom for the old-sandbox teardown that happens
# after the new one is already healthy (not on the client-facing critical path).
PROVISION_TIMEOUT_S = float(os.environ.get("PROVISION_TIMEOUT_S", "110"))

# Files in agent-template that are Provisioner metadata, never copied into the sandbox.
TEMPLATE_EXCLUDE_NAMES = {"forge.manifest.json", "README.md", ".DS_Store", "__pycache__"}

# Names that must never be logged or echoed back, matched case-insensitively as substrings.
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


def require_daytona_key() -> str:
    if not DAYTONA_API_KEY:
        raise RuntimeError(f"DAYTONA_API_KEY is not set (checked {REPO_ROOT / '.env'})")
    return DAYTONA_API_KEY
