import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RegistryEntry:
    slug: str
    sandbox_id: str
    chat_url: str
    spec_hash: str
    updated_at: float


def spec_hash(spec_dict: dict) -> str:
    canonical = json.dumps(spec_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SandboxRegistry:
    """In-memory Map<slug, sandbox_id> (+ chat_url, spec_hash) per PRD §4.1.

    Per-slug locks so two concurrent /forge calls for the *same* business
    can't both think they're the one replacing the current sandbox; calls
    for different businesses never block each other.
    """

    def __init__(self):
        self._entries: dict[str, RegistryEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def lock_for(self, slug: str) -> asyncio.Lock:
        async with self._locks_guard:
            if slug not in self._locks:
                self._locks[slug] = asyncio.Lock()
            return self._locks[slug]

    def get(self, slug: str) -> Optional[RegistryEntry]:
        return self._entries.get(slug)

    def put(self, slug: str, sandbox_id: str, chat_url: str, spec_hash_value: str) -> None:
        self._entries[slug] = RegistryEntry(
            slug=slug,
            sandbox_id=sandbox_id,
            chat_url=chat_url,
            spec_hash=spec_hash_value,
            updated_at=time.time(),
        )


registry = SandboxRegistry()
