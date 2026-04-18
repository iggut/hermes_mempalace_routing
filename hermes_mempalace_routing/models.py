from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RawArtifact:
    artifact_id: str
    turn_id: str
    kind: str
    created_at: str
    path: str
    mime_type: str = "text/plain"
    size_bytes: int = 0
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryEnvelope:
    memory_id: str
    room: str
    route_tags: list[str] = field(default_factory=list)
    fact_type: str = "note"
    summary: str = ""
    provenance_artifact_ids: list[str] = field(default_factory=list)
    provenance_excerpt: str | None = None
    confidence: float = 0.5
    pinned: bool = False
    conflict_key: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteCandidate:
    room: str
    memory_id: str
    score: float
    rationale: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContextBudget:
    """Fractions: 20% live, 35% routed memory, 15% raw diagnostics, 10% reserve; remainder is unallocated."""

    total_tokens: int
    live_conversation: int
    routed_memory: int
    raw_diagnostics: int
    reserve: int
    remainder: int


@dataclass(slots=True)
class InjectedEvidence:
    memory_id: str
    room: str
    summary: str
    provenance: list[str]
    raw_excerpt: str | None = None


@dataclass(slots=True)
class RawDiagnosticExcerpt:
    """Exact bytes from disk; length cap applies only at prompt assembly (outbound path)."""

    artifact_id: str
    memory_id: str
    room: str
    text: str


@dataclass(slots=True)
class ConflictRecord:
    conflict_key: str
    room: str
    candidate_memory_ids: list[str]
    resolved_memory_id: str | None = None
    resolution_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
