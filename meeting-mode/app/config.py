import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

# Provisioner (Dev A's own /forge). Never exposed to the frontend directly —
# the browser only ever talks to Meeting Mode's own API.
PROVISIONER_URL = os.environ.get("PROVISIONER_URL", "http://127.0.0.1:8899")
PROVISION_TIMEOUT_S = float(os.environ.get("PROVISION_TIMEOUT_S", "110"))

# LLM for ForgeSpec generation (OpenAI-compatible chat completions).
# Originally Kimi per PRD; api.kimi.ai doesn't resolve (confirmed DNS
# NXDOMAIN) and the sponsor key turned out to be a Doubleword.ai key, so
# this now points there. Model swap only — the "one Kimi call" contract
# in PRD §5 step 7 is otherwise unchanged.
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL")
LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-20b")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "60"))

# Nosana (optional enrichment path for transcription; must never block the
# core pipeline — falls back to local whisper on any failure).
NOSANA_API_KEY = os.environ.get("NOSANA_API_KEY")
NOSANA_WHISPER_URL = os.environ.get("NOSANA_WHISPER_URL")
NOSANA_TIMEOUT_S = float(os.environ.get("NOSANA_TIMEOUT_S", "45"))

# Local whisper fallback
LOCAL_WHISPER_MODEL = os.environ.get("LOCAL_WHISPER_MODEL", "tiny")
LOCAL_WHISPER_LANGUAGE = os.environ.get("LOCAL_WHISPER_LANGUAGE", "id")

# Oxylabs Web Scraper API (optional pre-meeting research enrichment; never
# blocks the pipeline). Real API (confirmed against developers.oxylabs.io):
# https://realtime.oxylabs.io/v1/queries, HTTP Basic Auth with a
# username/password pair — not a single bearer token. OXYLABS_USERNAME/
# OXYLABS_PASSWORD are preferred; OXYLABS_KEY (this repo's original single
# placeholder var) is accepted as a "username:password" fallback if it's
# ever set in that shape, but the real API has no token-only auth mode, so
# a bare token there won't authenticate.
OXYLABS_USERNAME = os.environ.get("OXYLABS_USERNAME")
OXYLABS_PASSWORD = os.environ.get("OXYLABS_PASSWORD")
if not (OXYLABS_USERNAME and OXYLABS_PASSWORD):
    _legacy = os.environ.get("OXYLABS_KEY", "")
    if ":" in _legacy:
        OXYLABS_USERNAME, OXYLABS_PASSWORD = _legacy.split(":", 1)
# os.environ.get(key, default) only falls back to `default` when the key
# is ABSENT — an .env line like "OXYLABS_SCRAPER_URL=" (present, empty)
# still wins and yields "". Caught live: .env had exactly that. `or`
# handles both "absent" and "present but empty" the same way.
OXYLABS_SCRAPER_URL = os.environ.get("OXYLABS_SCRAPER_URL") or "https://realtime.oxylabs.io/v1/queries"
# 45s default: render="html" (headless-browser JS execution, see
# research.py) is noticeably slower than a plain fetch. Fine here — this
# runs pre-meeting, never on the Gate 1/2 critical path.
OXYLABS_TIMEOUT_S = float(os.environ.get("OXYLABS_TIMEOUT_S", "45"))
# Display-length cap, used on already-parsed/structured text (search
# result snippets). NOT the raw-HTML cap — see OXYLABS_RAW_HTML_MAX_CHARS.
OXYLABS_MAX_CONTENT_CHARS = int(os.environ.get("OXYLABS_MAX_CONTENT_CHARS", "20000"))
# Safety net on the raw HTML fetched from a page, applied BEFORE parsing.
# Must stay large — a rendered page's <head> (inline JS, tracking
# scripts) can alone run past a small cap, starving the parser of the
# actual <body> text before it ever sees it (this happened at the old
# 20,000 default; oxylabs.io's own head content filled the whole budget).
OXYLABS_RAW_HTML_MAX_CHARS = int(os.environ.get("OXYLABS_RAW_HTML_MAX_CHARS", "2000000"))

RESEARCH_TTL_S = float(os.environ.get("RESEARCH_TTL_S", "3600"))

# Storage: outside any public/static directory, never served directly.
JOBS_DIR = Path(os.environ.get("MEETING_MODE_JOBS_DIR", Path(__file__).resolve().parent.parent / ".jobs")).resolve()
JOBS_DIR.mkdir(parents=True, exist_ok=True)

JOB_TTL_S = float(os.environ.get("JOB_TTL_S", "3600"))  # abandoned-job sweep
JOB_SWEEP_INTERVAL_S = float(os.environ.get("JOB_SWEEP_INTERVAL_S", "300"))

# Upload limits
AUDIO_MAX_MB = float(os.environ.get("AUDIO_MAX_MB", "200"))
CLIENT_FILE_MAX_MB = float(os.environ.get("CLIENT_FILE_MAX_MB", "25"))

ALLOWED_AUDIO_EXTENSIONS = {".webm", ".ogg", ".wav", ".mp3", ".m4a"}
ALLOWED_AUDIO_MIME_PREFIXES = ("audio/", "video/webm")  # some browsers tag webm audio as video/webm

ALLOWED_PDF_EXTENSIONS = {".pdf"}
ALLOWED_EXCEL_EXTENSIONS = {".xlsx", ".xls"}
