from __future__ import annotations

from collections import defaultdict

from .models import ConflictRecord, MemoryEnvelope


class ConflictResolver:
    def detect(self, envelopes: list[MemoryEnvelope]) -> list[ConflictRecord]:
        groups: dict[str, list[MemoryEnvelope]] = defaultdict(list)
        for env in envelopes:
            if env.conflict_key:
                groups[env.conflict_key].append(env)

        conflicts: list[ConflictRecord] = []
        for key, group in groups.items():
            if len(group) < 2:
                continue
            pinned = [env for env in group if env.pinned]
            resolved = pinned[0].memory_id if pinned else None
            reason = "pinned" if pinned else None
            conflicts.append(
                ConflictRecord(
                    conflict_key=key,
                    room=group[0].room,
                    candidate_memory_ids=[env.memory_id for env in group],
                    resolved_memory_id=resolved,
                    resolution_reason=reason,
                )
            )
        return conflicts
