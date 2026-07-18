"""
One transcription interface, two providers behind it (PRD §2 item 1 /
§4: "Nosana whisper job; local whisper fallback"). Callers only ever see
`transcribe()` and `TranscriptionResult` — provider selection and fallback
happen inside.

Nosana contract note: NOSANA_WHISPER_URL/NOSANA_API_KEY were not set in
this repo's .env at build time (confirmed empty — Nosana is stretch-only
per PRD §9), so the Nosana branch below is written against a *documented
assumption* (multipart audio upload, JSON `{"text": ...}` response) and
was not exercised against a live endpoint. Any failure — wrong shape,
timeout, non-2xx, connection error — falls through to local whisper
automatically; it does not crash the pipeline.
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import requests

from . import config

log = logging.getLogger("meeting-mode")

Provider = Literal["nosana", "local_whisper"]

_local_model = None  # lazy-loaded, cached across calls in this process


class TranscriptionError(Exception):
    pass


@dataclass
class TranscriptionResult:
    text: str
    provider: Provider
    duration_s: float
    status: Literal["ok"] = "ok"


def _nosana_configured() -> bool:
    return bool(config.NOSANA_API_KEY and config.NOSANA_WHISPER_URL)


def _try_nosana(audio_path: Path) -> str | None:
    if not _nosana_configured():
        return None
    try:
        with open(audio_path, "rb") as f:
            resp = requests.post(
                config.NOSANA_WHISPER_URL,
                headers={"Authorization": f"Bearer {config.NOSANA_API_KEY}"},
                files={"file": (audio_path.name, f)},
                timeout=config.NOSANA_TIMEOUT_S,
            )
        resp.raise_for_status()
        text = resp.json().get("text")
        if not text or not text.strip():
            log.warning("nosana returned no text, falling back to local whisper")
            return None
        return text.strip()
    except Exception as exc:
        log.warning("nosana transcription failed (%s), falling back to local whisper", exc)
        return None


def _load_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel
        log.info("loading local whisper model=%s (first call only)", config.LOCAL_WHISPER_MODEL)
        _local_model = WhisperModel(config.LOCAL_WHISPER_MODEL, device="cpu", compute_type="int8")
    return _local_model


def _run_local_whisper(audio_path: Path) -> tuple[str, float]:
    model = _load_local_model()
    # Language pinned, not auto-detected: caught live in Gate 2 rehearsal
    # (tiny model misdetected clearly-Indonesian speech as English, 98%
    # confidence). Every meeting in this product is Indonesian (PRD
    # personas are all "id") — auto-detect has nothing to gain and a
    # wrong guess measurably hurts transcription quality.
    segments, info = model.transcribe(str(audio_path), language=config.LOCAL_WHISPER_LANGUAGE)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, float(info.duration or 0.0)


def transcribe(audio_path: Path) -> TranscriptionResult:
    t0 = time.monotonic()

    nosana_text = _try_nosana(audio_path)
    if nosana_text:
        return TranscriptionResult(
            text=nosana_text,
            provider="nosana",
            duration_s=time.monotonic() - t0,
        )

    try:
        text, audio_duration_s = _run_local_whisper(audio_path)
    except Exception as exc:
        raise TranscriptionError(f"local whisper failed: {exc}") from exc

    if not text:
        # PRD: "Do not silently return an empty transcript" — an empty
        # result is a failure state, not a valid (if boring) transcript.
        raise TranscriptionError(
            "transcription produced no text (silent/unintelligible audio, or unsupported language)"
        )

    return TranscriptionResult(
        text=text,
        provider="local_whisper",
        duration_s=audio_duration_s,
    )
