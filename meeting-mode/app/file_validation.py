import uuid
from pathlib import Path

from . import config


class FileValidationError(Exception):
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def _ext(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def _sniff(head: bytes) -> str | None:
    """Best-effort magic-byte sniff. Client-supplied Content-Type is never
    trusted alone — this catches a renamed .exe posing as .pdf, etc."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "zip"  # .xlsx is a zip container
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"  # legacy .xls
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:3] == b"ID3" or head[:2] == b"\xff\xfb":
        return "mp3"
    if head[4:8] == b"ftyp":
        return "mp4"  # covers .m4a
    return None


def validate_audio_upload(filename: str, content_type: str | None, data: bytes) -> None:
    if not data:
        raise FileValidationError("audio", "file is empty")
    size_mb = len(data) / (1024 * 1024)
    if size_mb > config.AUDIO_MAX_MB:
        raise FileValidationError("audio", f"exceeds max size of {config.AUDIO_MAX_MB}MB")

    ext = _ext(filename)
    if ext not in config.ALLOWED_AUDIO_EXTENSIONS:
        raise FileValidationError(
            "audio", f"unsupported extension {ext or '(none)'}; allowed: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"
        )

    kind = _sniff(data[:64])
    if kind not in {"wav", "ogg", "webm", "mp3", "mp4"}:
        raise FileValidationError("audio", "file content does not look like a supported audio format")


def validate_client_file(filename: str, content_type: str | None, data: bytes) -> str:
    """Returns the resolved kind: 'pdf' or 'excel'."""
    if not data:
        raise FileValidationError(filename, "file is empty")
    size_mb = len(data) / (1024 * 1024)
    if size_mb > config.CLIENT_FILE_MAX_MB:
        raise FileValidationError(filename, f"exceeds max size of {config.CLIENT_FILE_MAX_MB}MB")

    ext = _ext(filename)
    kind = _sniff(data[:64])

    if ext in config.ALLOWED_PDF_EXTENSIONS:
        if kind != "pdf":
            raise FileValidationError(filename, "extension is .pdf but content is not a valid PDF")
        return "pdf"

    if ext in config.ALLOWED_EXCEL_EXTENSIONS:
        if kind not in {"zip", "ole"}:
            raise FileValidationError(filename, "extension is Excel but content is not a valid spreadsheet")
        return "excel"

    raise FileValidationError(
        filename,
        f"unsupported file type {ext or '(none)'}; only PDF and Excel client files are accepted",
    )


def safe_temp_filename(original_filename: str) -> str:
    """Never trust the client's filename for a path. Keep only the
    extension; the stored name is otherwise fully server-generated."""
    ext = _ext(original_filename)
    return f"{uuid.uuid4().hex}{ext}"
