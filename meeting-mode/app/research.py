"""
Pre-meeting enrichment via the Oxylabs Web Scraper API (PRD §2 stretch:
"Oxylabs pre-research on the client's business"). Runs BEFORE the meeting,
outside the transcribe->extract->generate chain — never on the Gate 1/2
critical path. Optional in every sense: if it fails, times out, or is
never requested, Meeting Mode proceeds exactly as it would without it.

Untested against a live endpoint: OXYLABS_USERNAME/OXYLABS_PASSWORD are
not set in this repo's .env (confirmed empty at build time, same as the
Nosana path — both are PRD §9 "stretch only"). The request/response shape
below is built against Oxylabs' real documented Realtime API
(developers.oxylabs.io/products/web-scraper-api), not guessed, but has
not been exercised against a live account. Verified in this session via
simulated failures: the graceful-skip path (missing creds, timeout,
non-200, empty content) all correctly downgrade to "no enrichment" rather
than raising into the caller.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

import requests

from . import config

log = logging.getLogger("meeting-mode")


class ResearchError(Exception):
    """Raised internally only; callers should prefer research_or_none()
    which never raises — this exists so tests can assert on failure
    reasons without parsing log strings."""


@dataclass
class EnrichmentResult:
    business_name: Optional[str] = None
    description: Optional[str] = None
    products_or_services: Optional[str] = None
    location: Optional[str] = None
    hours: Optional[str] = None
    contact: Optional[str] = None
    policy_snippets: Optional[str] = None
    source_urls: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.business_name, self.description, self.products_or_services,
            self.location, self.hours, self.contact, self.policy_snippets,
        ])

    def to_prompt_text(self) -> str:
        """Rendered for the LLM call — PRD §5 step 6/7: fed as context,
        never as ground truth. Wording here (not the system prompt alone)
        also reinforces it's the lowest-priority source."""
        lines = ["(lowest priority vs transcript and client files; public web data, may be stale or wrong)"]
        if self.business_name:
            lines.append(f"Business name (web): {self.business_name}")
        if self.description:
            lines.append(f"Description (web): {self.description}")
        if self.products_or_services:
            lines.append(f"Products/services (web): {self.products_or_services}")
        if self.location:
            lines.append(f"Location (web): {self.location}")
        if self.hours:
            lines.append(f"Hours (web): {self.hours}")
        if self.contact:
            lines.append(f"Public contact (web): {self.contact}")
        if self.policy_snippets:
            lines.append(f"Policy info (web): {self.policy_snippets}")
        if self.source_urls:
            lines.append(f"Source(s): {', '.join(self.source_urls)}")
        return "\n".join(lines)


class _TextExtractor(HTMLParser):
    """Minimal HTML->text: title, meta description, and visible body text.
    Not a real parser/renderer — good enough for a best-effort research
    signal that the LLM treats as low-priority context, not for anything
    that needs to be exact."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self._in_title = False
        self._skip_tags = {"script", "style", "noscript"}
        self._skip_depth = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self._skip_tags:
            self._skip_depth += 1
        if tag == "meta":
            attr_dict = dict(attrs)
            if attr_dict.get("name", "").lower() == "description" and attr_dict.get("content"):
                self.meta_description = attr_dict["content"].strip()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self.title += stripped
        else:
            self.text_parts.append(stripped)


# Accessibility skip-links and cookie-banner boilerplate are near-universal
# and sit right at the top of the DOM — exactly where a naive "first N
# chars of body text" grab lands. Stripped so a combined meta+body
# description doesn't read as junk appended after the real summary
# (caught live: "...Extract public data from any website with ease! —
# Skip to main content").
_BOILERPLATE_PREFIXES = re.compile(
    r"^\s*(skip to (main )?content|skip to navigation|accept (all )?cookies|"
    r"we use cookies[^.]*\.?)\s*",
    re.IGNORECASE,
)


def _extract_text(html: str) -> tuple[str, str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    body_text = " ".join(parser.text_parts)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    # Boilerplate can repeat (e.g. a skip-link followed by a cookie
    # banner) — strip iteratively, not just once.
    while True:
        stripped = _BOILERPLATE_PREFIXES.sub("", body_text)
        if stripped == body_text:
            break
        body_text = stripped.strip()
    return parser.title.strip(), parser.meta_description.strip(), body_text


def _oxylabs_configured() -> bool:
    return bool(config.OXYLABS_USERNAME and config.OXYLABS_PASSWORD)


def _fetch_universal(url: str) -> tuple[str, str]:
    """One Oxylabs 'universal' source call for a known URL. Returns
    (result_url, html). Raises ResearchError on any failure.

    render="html" caught live: without it, a JS-rendered site (e.g.
    oxylabs.io itself, a React/Next.js marketing page) returns raw HTML
    with only <title>/<meta> populated server-side and a genuinely EMPTY
    <body> — no amount of text-combining logic can produce a longer
    summary from zero real content. render="html" has Oxylabs execute JS
    via a headless browser first, so the returned HTML has the actual
    rendered page text. Slower per call, but this endpoint runs
    pre-meeting, off the Gate 1/2 critical path, so the extra latency is
    an acceptable trade for actually having something to summarize."""
    payload = {"source": "universal", "url": url, "render": "html", "parse": False}
    resp = requests.post(
        config.OXYLABS_SCRAPER_URL,
        auth=(config.OXYLABS_USERNAME, config.OXYLABS_PASSWORD),
        json=payload,
        timeout=config.OXYLABS_TIMEOUT_S,
    )
    if resp.status_code != 200:
        raise ResearchError(f"Oxylabs returned HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    results = body.get("results") or []
    if not results:
        raise ResearchError("Oxylabs response had no results")
    result = results[0]
    content = result.get("content")
    if not content or not isinstance(content, str):
        raise ResearchError("Oxylabs result had no usable content")
    # Truncating the RAW html here (before parsing) was the actual root
    # cause of "summary too short": a rendered page's <head> (inline JS
    # bundles, tracking scripts) can alone exceed a small char budget,
    # so the real <body> text never reached the parser at all — confirmed
    # live, html length landed at exactly the old cap with an empty body
    # result. Cap is now large (raw HTML safety limit, not a display
    # limit) and applied only as a final safety net; the actual
    # human-readable length limit is DESCRIPTION_MAX_CHARS, applied to
    # the *parsed* text in _build_description, well after this.
    return result.get("url", url), content[: config.OXYLABS_RAW_HTML_MAX_CHARS]


def _fetch_search(company_name: str) -> tuple[str, str]:
    """One Oxylabs 'google_search' call to find the business's own site
    when only a company name is given (no URL). Returns
    (source_description, text_summary) built from organic result
    titles/descriptions/URLs — does not follow through to a second fetch,
    keeping this to one Oxylabs call as the timeout/latency budget."""
    payload = {"source": "google_search", "query": company_name, "parse": True}
    resp = requests.post(
        config.OXYLABS_SCRAPER_URL,
        auth=(config.OXYLABS_USERNAME, config.OXYLABS_PASSWORD),
        json=payload,
        timeout=config.OXYLABS_TIMEOUT_S,
    )
    if resp.status_code != 200:
        raise ResearchError(f"Oxylabs returned HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    results = body.get("results") or []
    if not results:
        raise ResearchError("Oxylabs search returned no results")
    content = results[0].get("content")
    if not isinstance(content, dict):
        raise ResearchError("Oxylabs search result had unexpected shape")
    organic = content.get("results", {}).get("organic") or []
    if not organic:
        raise ResearchError("Oxylabs search returned no organic results")

    lines = []
    urls = []
    for item in organic[:3]:
        title = item.get("title", "")
        desc = item.get("desc") or item.get("description", "")
        url = item.get("url", "")
        if title or desc:
            lines.append(f"{title}: {desc}".strip(": "))
        if url:
            urls.append(url)
    return ", ".join(urls), "\n".join(lines)[: config.OXYLABS_MAX_CONTENT_CHARS]


# Meta descriptions are often a one-line tagline (or, for a bot-protection/
# consent page, just a few words) — using it alone produced summaries that
# read as too short even when the page had more real text to offer.
# Combine both, meta first (it's usually the more deliberately-written
# summary when substantial), body text appended for depth, deduplicating
# so the meta line doesn't just repeat verbatim inside the body excerpt.
DESCRIPTION_MAX_CHARS = 700


def _build_description(meta_desc: str, body_text: str) -> Optional[str]:
    parts: list[str] = []
    if meta_desc:
        parts.append(meta_desc)

    if body_text:
        # Drop only the duplicated portion (meta_desc often literally
        # reappears as the page's H1/hero text), not the whole body —
        # first attempt dropped the entire body excerpt whenever meta_desc
        # showed up anywhere in it, which just gave back meta_desc alone
        # even on pages with plenty more real content available.
        remainder = body_text.replace(meta_desc, "", 1) if meta_desc else body_text
        remainder = remainder.strip(" —-")
        budget = DESCRIPTION_MAX_CHARS - len(" — ".join(parts))
        if remainder and budget > 40:  # not worth appending a tiny scrap
            parts.append(remainder[:budget])

    if not parts:
        return None
    return " — ".join(parts)[:DESCRIPTION_MAX_CHARS]


def run_research(company_name: Optional[str], website: Optional[str]) -> tuple[Optional[EnrichmentResult], Optional[str]]:
    """Never raises. Returns (result_or_None, warning_or_None). A None
    result with a warning means: proceed without enrichment, tell the
    user why in a non-blocking way (PRD §8: 'continue... show a
    non-blocking warning... do not fail Gate 1 or 2')."""
    if not company_name and not website:
        return None, "provide a company name or website"

    if not _oxylabs_configured():
        log.info("research skipped: OXYLABS_USERNAME/OXYLABS_PASSWORD not configured")
        return None, "pre-meeting research is not configured (missing Oxylabs credentials)"

    t0 = time.monotonic()
    try:
        if website:
            source_url, html = _fetch_universal(website)
            title, meta_desc, body_text = _extract_text(html)
            result = EnrichmentResult(
                business_name=title or company_name,
                description=_build_description(meta_desc, body_text),
                location=_guess_field(body_text, ["alamat", "lokasi", "location", "address", "jakarta", "bandung", "surabaya", "yogyakarta"]),
                hours=_guess_field(body_text, ["jam buka", "jam operasional", "hours", "buka", "senin", "monday"]),
                contact=_guess_field(body_text, ["kontak", "whatsapp", "telepon", "contact", "email"]),
                source_urls=[source_url],
            )
        else:
            source_desc, text = _fetch_search(company_name)
            result = EnrichmentResult(
                business_name=company_name,
                description=text or None,
                source_urls=[u for u in source_desc.split(", ") if u],
            )
    except ResearchError as exc:
        duration = time.monotonic() - t0
        log.warning("research failed after %.1fs: %s", duration, exc)
        return None, f"pre-meeting research failed ({exc}), continuing without it"
    except requests.RequestException as exc:
        duration = time.monotonic() - t0
        log.warning("research request error after %.1fs: %s", duration, exc)
        return None, "pre-meeting research timed out or was unreachable, continuing without it"
    except Exception as exc:
        log.exception("research: unexpected error")
        return None, f"pre-meeting research failed unexpectedly, continuing without it"

    duration = time.monotonic() - t0
    if result.is_empty():
        log.warning("research completed in %.1fs but produced no usable content", duration)
        return None, "pre-meeting research returned no usable content, continuing without it"

    log.info(
        "research ok duration=%.1fs sources=%s fields_populated=%d",
        duration, result.source_urls, sum(1 for v in (
            result.business_name, result.description, result.products_or_services,
            result.location, result.hours, result.contact, result.policy_snippets,
        ) if v),
    )
    return result, None


def _guess_field(text: str, keywords: list[str]) -> Optional[str]:
    """Cheapest-possible heuristic: find a sentence-ish window around the
    first matching keyword. This is explicitly a best-effort signal, not
    a claim of accuracy — it's handed to the LLM as low-priority context,
    same as everything else in EnrichmentResult."""
    if not text:
        return None
    lower = text.lower()
    for kw in keywords:
        idx = lower.find(kw.lower())
        if idx != -1:
            window = text[max(0, idx - 40): idx + 120]
            return window.strip()
    return None
