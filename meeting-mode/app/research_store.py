"""Short-lived store for pre-meeting enrichment results, keyed by a
research_id the frontend carries from the research form to the eventual
Meeting Mode job submission (PRD requirement 5). Same TTL-sweep pattern as
job_store.py, deliberately not merged with it — research can happen well
before a recording session exists."""
import time
from dataclasses import dataclass
from typing import Optional

from . import config
from .research import EnrichmentResult


@dataclass
class ResearchEntry:
    enrichment: Optional[EnrichmentResult]
    warning: Optional[str]
    created_at: float


class ResearchStore:
    def __init__(self):
        self._entries: dict[str, ResearchEntry] = {}

    def put(self, research_id: str, enrichment: Optional[EnrichmentResult], warning: Optional[str]) -> None:
        self._entries[research_id] = ResearchEntry(enrichment, warning, time.monotonic())
        self._sweep()

    def get(self, research_id: str) -> Optional[ResearchEntry]:
        self._sweep()
        entry = self._entries.get(research_id)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at > config.RESEARCH_TTL_S:
            self._entries.pop(research_id, None)
            return None
        return entry

    def _sweep(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._entries.items() if now - v.created_at > config.RESEARCH_TTL_S]
        for k in stale:
            self._entries.pop(k, None)


store = ResearchStore()
