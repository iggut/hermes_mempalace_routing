from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .mempalace_adapter import MemPalaceAdapter, MemPalaceAdapterError
from .mempalace_scope import (
    MEMPALACE_DURABLE_TAG,
    MEMPALACE_VERBATIM_TAG,
    ScopeSanitizeError,
    derive_mempalace_scope,
    sanitize_drawer_content,
)
from .models import ClassificationSource, MemoryEnvelope, VerificationStatus
from .redaction import redact_text

if TYPE_CHECKING:
    from .config import RoutingConfig
    from .storage import StorageBackend


def build_mempalace_envelope_after_drawer(
    *,
    drawer_id: str,
    room: str,
    summary: str,
    verbatim: str,
    fact_type: str,
    route_tags: list[str] | None,
    pinned: bool,
    conflict_key: str | None,
) -> MemoryEnvelope:
    """SQLite envelope row referencing a MemPalace drawer (verbatim in provenance_excerpt, no local raw file)."""
    now = datetime.now(UTC).isoformat()
    tags = list(route_tags or [])
    if MEMPALACE_VERBATIM_TAG not in tags:
        tags.append(MEMPALACE_VERBATIM_TAG)
    if MEMPALACE_DURABLE_TAG not in tags:
        tags.append(MEMPALACE_DURABLE_TAG)
    return MemoryEnvelope(
        memory_id=f"mem_mp_{drawer_id}",
        room=room,
        route_tags=tags,
        fact_type=fact_type,
        summary=summary,
        provenance_artifact_ids=[],
        provenance_excerpt=verbatim,
        confidence=0.85,
        pinned=pinned,
        conflict_key=conflict_key,
        created_at=now,
        updated_at=now,
        classification_source=ClassificationSource.RULE.value,
        verification_status=VerificationStatus.UNVERIFIED.value,
    )


def durable_write_via_mempalace(
    *,
    storage: StorageBackend,
    config: RoutingConfig,
    adapter: MemPalaceAdapter,
    turn_id: str,
    room: str,
    fact_type: str,
    summary: str,
    raw_text: str,
    route_tags: list[str] | None,
    conflict_key: str | None,
    pinned: bool,
    active_project: str | None,
) -> MemoryEnvelope | None:
    """
    Verbatim durable path: sanitize → redact (optional) → duplicate check → add_drawer → envelope row.

    Does not write a local raw artifact file (MemPalace holds verbatim bytes).
    """
    del turn_id  # provenance can be carried in metadata for MemPalace tools
    try:
        scope = derive_mempalace_scope(
            active_project=active_project,
            fact_type=fact_type,
            room_hint=room,
            wing_strategy=config.mempalace_default_wing_strategy,
            room_strategy=config.mempalace_default_room_strategy,
        )
        wing = scope.wing
        room_filed = scope.room
        text = raw_text
        if config.redact_before_persist:
            rr = redact_text(text, config.redaction_policy)
            text = rr.text
            if rr.status == "dropped":
                return None
        verbatim = sanitize_drawer_content(text)
    except ScopeSanitizeError:
        return None

    try:
        is_dup, _dup_id = adapter.check_duplicate(
            verbatim,
            wing,
            room_filed,
            threshold=config.mempalace_duplicate_threshold,
        )
    except MemPalaceAdapterError:
        if config.mempalace_fail_open:
            return None
        raise

    if is_dup and not config.mempalace_allow_duplicate_supersede:
        return None

    meta: dict[str, Any] = {
        "source": "hermes_mempalace_routing",
        "fact_type": fact_type,
        "summary": summary,
    }
    try:
        drawer_id = adapter.add_drawer(verbatim, wing, room_filed, metadata=meta)
    except MemPalaceAdapterError:
        if config.mempalace_fail_open:
            return None
        raise

    env = build_mempalace_envelope_after_drawer(
        drawer_id=drawer_id,
        room=room_filed,
        summary=summary,
        verbatim=verbatim,
        fact_type=fact_type,
        route_tags=route_tags,
        pinned=pinned,
        conflict_key=conflict_key,
    )
    try:
        storage.append_envelope(env)
    except Exception:
        if config.mempalace_fail_open:
            return None
        raise
    return env
