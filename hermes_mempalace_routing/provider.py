from __future__ import annotations

from datetime import UTC, datetime

from .models import MemoryEnvelope
from .storage import RoutingStorage


class MemPalaceRoutingProvider:
    def __init__(self, storage: RoutingStorage):
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
        route_tags = route_tags or []
        raw = self.storage.persist_raw_artifact(turn_id=turn_id, kind=fact_type, text=raw_text)
        now = datetime.now(UTC).isoformat()
        env = MemoryEnvelope(
            memory_id=f"mem_{raw.artifact_id}",
            room=room,
            route_tags=route_tags,
            fact_type=fact_type,
            summary=summary,
            provenance_artifact_ids=[raw.artifact_id],
            provenance_excerpt=raw_text[:300] if raw_text else None,
            confidence=0.9 if fact_type in {"stacktrace", "shell_output", "tool_output"} else 0.7,
            pinned=pinned,
            conflict_key=conflict_key,
            created_at=now,
            updated_at=now,
        )
        self.storage.append_envelope(env)
        if pinned:
            self.storage.append_pin(env.memory_id, "created pinned")
        return env
