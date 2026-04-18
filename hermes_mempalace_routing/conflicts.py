from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from .config import RoutingConfig
from .models import ConflictRecord, ConflictStatus, MemoryEnvelope, VerificationStatus


def _parse_created_at(created_at: str) -> datetime | None:
    if not created_at:
        return None
    try:
        s = created_at.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _has_runtime_truth_tag(env: MemoryEnvelope) -> bool:
    return any(str(t).lower() == "runtime_truth" for t in env.route_tags)


def choose_effective_memory(
    group: list[MemoryEnvelope],
    config: RoutingConfig,
) -> tuple[str | None, str, list[str], str]:
    """
    Return (winner_id, status, losers, reason_code).

    status is a ConflictStatus value or unresolved.
    """
    if len(group) < 2:
        return None, ConflictStatus.UNRESOLVED.value, [], "insufficient_candidates"

    cands = list(group)
    order = list(config.conflict_precedence)

    for rule in order:
        if rule == "runtime_truth":
            tagged = [e for e in cands if _has_runtime_truth_tag(e)]
            if len(tagged) == 1:
                w = tagged[0]
                losers = [e.memory_id for e in group if e.memory_id != w.memory_id]
                return w.memory_id, ConflictStatus.RESOLVED_BY_RUNTIME_TRUTH.value, losers, "runtime_truth_unique"
            if len(tagged) > 1:
                cands = tagged
                continue
            continue

        if rule == "pin":
            pinned = [e for e in cands if e.pinned]
            if len(pinned) == 1:
                w = pinned[0]
                losers = [e.memory_id for e in group if e.memory_id != w.memory_id]
                return w.memory_id, ConflictStatus.RESOLVED_BY_PIN.value, losers, "pin_unique"
            if len(pinned) > 1:
                cands = pinned
                continue
            continue

        if rule == "newer_verified":
            def sort_key(e: MemoryEnvelope) -> tuple[int, float, str]:
                ver = 1 if e.verification_status == VerificationStatus.VERIFIED.value else 0
                dt = _parse_created_at(e.created_at)
                ts = dt.timestamp() if dt else 0.0
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                    ts = dt.timestamp()
                return (ver, ts, e.memory_id)

            ranked = sorted(cands, key=sort_key, reverse=True)
            best = sort_key(ranked[0])
            top = [e for e in ranked if sort_key(e) == best]
            if len(top) == 1:
                w = top[0]
                losers = [e.memory_id for e in group if e.memory_id != w.memory_id]
                return w.memory_id, ConflictStatus.RESOLVED_BY_NEWER_VERIFIED.value, losers, "newer_verified_tiebreak"
            cands = top
            continue

    return None, ConflictStatus.UNRESOLVED.value, [], "unresolved"


def detect_conflicts(
    envelopes: list[MemoryEnvelope],
    config: RoutingConfig,
    *,
    stored: list[ConflictRecord] | None = None,
) -> list[ConflictRecord]:
    """
    Detect conflict groups by conflict_key.

    If a row exists in ``stored`` for a key, operator resolution wins unless candidates changed.
    """
    stored_by_key: dict[str, ConflictRecord] = {c.conflict_key: c for c in (stored or [])}
    groups: dict[str, list[MemoryEnvelope]] = defaultdict(list)
    for env in envelopes:
        if env.conflict_key:
            groups[env.conflict_key].append(env)

    out: list[ConflictRecord] = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        room = group[0].room
        candidate_ids = sorted({e.memory_id for e in group})

        prev = stored_by_key.get(key)
        if prev is not None:
            ids_set = set(candidate_ids)
            prev_cands = set(prev.candidate_memory_ids)
            if ids_set == prev_cands and prev.resolved_memory_id and prev.status != ConflictStatus.UNRESOLVED.value:
                losers = [m for m in candidate_ids if m != prev.resolved_memory_id]
                out.append(
                    ConflictRecord(
                        conflict_key=key,
                        room=room,
                        candidate_memory_ids=candidate_ids,
                        resolved_memory_id=prev.resolved_memory_id,
                        loser_memory_ids=losers,
                        resolution_reason=prev.resolution_reason,
                        status=prev.status,
                        resolution_actor=prev.resolution_actor,
                        resolved_at=prev.resolved_at,
                    )
                )
                continue

        winner, status, losers, _reason = choose_effective_memory(group, config)
        res_reason: str | None
        if status == ConflictStatus.UNRESOLVED.value:
            res_reason = None
        elif status == ConflictStatus.RESOLVED_BY_PIN.value:
            res_reason = "precedence_pin"
        elif status == ConflictStatus.RESOLVED_BY_RUNTIME_TRUTH.value:
            res_reason = "precedence_runtime_truth"
        elif status == ConflictStatus.RESOLVED_BY_NEWER_VERIFIED.value:
            res_reason = "precedence_newer_verified"
        else:
            res_reason = "precedence"

        out.append(
            ConflictRecord(
                conflict_key=key,
                room=room,
                candidate_memory_ids=candidate_ids,
                resolved_memory_id=winner,
                loser_memory_ids=losers,
                resolution_reason=res_reason,
                status=status,
                resolution_actor="system" if winner else None,
                resolved_at=None,
            )
        )
    return out


def resolve_conflict(
    *,
    conflict_key: str,
    winner_memory_id: str,
    actor: str,
    reason: str,
    envelopes: list[MemoryEnvelope],
) -> ConflictRecord:
    """Build a resolved conflict record (caller persists via storage)."""
    group = [e for e in envelopes if e.conflict_key == conflict_key]
    candidate_ids = sorted({e.memory_id for e in group})
    if winner_memory_id not in candidate_ids:
        raise ValueError(f"winner {winner_memory_id!r} not in conflict group {conflict_key!r}")
    losers = [m for m in candidate_ids if m != winner_memory_id]
    room = group[0].room if group else "unknown"
    now = datetime.now(UTC).isoformat()
    return ConflictRecord(
        conflict_key=conflict_key,
        room=room,
        candidate_memory_ids=candidate_ids,
        resolved_memory_id=winner_memory_id,
        loser_memory_ids=losers,
        resolution_reason=reason,
        status=ConflictStatus.RESOLVED_BY_PIN.value,
        resolution_actor=actor,
        resolved_at=now,
    )


def list_conflicts(
    envelopes: list[MemoryEnvelope],
    config: RoutingConfig,
    *,
    stored: list[ConflictRecord] | None = None,
) -> list[ConflictRecord]:
    """Merge stored resolutions with fresh detection."""
    return detect_conflicts(envelopes, config, stored=stored)


class ConflictResolver:
    """Back-compat wrapper around detect_conflicts."""

    def detect(
        self,
        envelopes: list[MemoryEnvelope],
        config: RoutingConfig | None = None,
        *,
        stored: list[ConflictRecord] | None = None,
    ) -> list[ConflictRecord]:
        cfg = config or RoutingConfig.default()
        return detect_conflicts(envelopes, cfg, stored=stored)
