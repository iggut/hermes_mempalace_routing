from __future__ import annotations

from collections import defaultdict

from .models import ConflictRecord, ConflictStatus, MemoryEnvelope


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
            status = ConflictStatus.RESOLVED_BY_PIN.value if pinned else ConflictStatus.UNRESOLVED.value
            conflicts.append(
                ConflictRecord(
                    conflict_key=key,
                    room=group[0].room,
                    candidate_memory_ids=[env.memory_id for env in group],
                    resolved_memory_id=resolved,
                    resolution_reason=reason,
                    status=status,
                    resolution_actor="operator" if pinned else None,
                )
            )
        return conflicts
