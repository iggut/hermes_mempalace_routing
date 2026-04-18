from __future__ import annotations

from .models import MemoryEnvelope
from .storage import StorageBackend


class MemPalaceRoutingProvider:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def store_artifact_as_memory(
        self,
        turn_id: str,
        room: str,
        fact_type: str,
        summary: str,
        raw_text: str,
        route_tags: list[str] | None = None,
        conflict_key: str | None = None,
        pinned: bool = False,
    ) -> MemoryEnvelope:
        return self.storage.persist_memory_turn(
            turn_id=turn_id,
            room=room,
            fact_type=fact_type,
            summary=summary,
            raw_text=raw_text,
            route_tags=route_tags,
            conflict_key=conflict_key,
            pinned=pinned,
        )
