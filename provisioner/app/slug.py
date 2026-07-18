import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DASH_RUN = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    """Stable, deterministic slug: lowercase, ascii-ish, dash-separated.

    Same input always produces the same output — this is the identity the
    registry keys on, so it must never depend on ordering, locale, or time.
    """
    s = name.strip().lower()
    s = _NON_ALNUM.sub("-", s)
    s = _DASH_RUN.sub("-", s).strip("-")
    return s[:63] or "business"
