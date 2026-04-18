from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ArtifactKind(str, Enum):
    """Controlled vocabulary for persisted raw artifact kinds."""

    NOTE = "note"
    STACKTRACE = "stacktrace"
    SHELL_OUTPUT = "shell_output"
    TOOL_OUTPUT = "tool_output"
    # Catch-all for forward compatibility
    OTHER = "other"


class FactType(str, Enum):
    NOTE = "note"
    STACKTRACE = "stacktrace"
    SHELL_OUTPUT = "shell_output"
    TOOL_OUTPUT = "tool_output"
    OTHER = "other"


class RedactionStatus(str, Enum):
    NONE = "none"
    MASKED = "masked"
    DROPPED = "dropped"


class ClassificationSource(str, Enum):
    RULE = "rule"
    OPERATOR = "operator"
    MODEL = "model"
    IMPORT = "import"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class ConflictStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED_BY_PIN = "resolved_by_pin"
    RESOLVED_BY_RUNTIME_TRUTH = "resolved_by_runtime_truth"
    RESOLVED_BY_NEWER_VERIFIED = "resolved_by_newer_verified"


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
    schema_version: int = 1
    redaction_status: str = RedactionStatus.NONE.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryEnvelope:
    memory_id: str
    room: str
    route_tags: list[str] = field(default_factory=list)
    fact_type: str = FactType.NOTE.value
    summary: str = ""
    provenance_artifact_ids: list[str] = field(default_factory=list)
    provenance_excerpt: str | None = None
    confidence: float = 0.5
    pinned: bool = False
    conflict_key: str | None = None
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = 1
    classification_source: str = ClassificationSource.RULE.value
    verification_status: str = VerificationStatus.UNVERIFIED.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteCandidate:
    room: str
    memory_id: str
    score: float
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "memory_id": self.memory_id,
            "score": self.score,
            "rationale": self.rationale,
        }


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
    status: str = ConflictStatus.UNRESOLVED.value
    resolution_actor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteRun:
    """Structured trace for a single build_context_for_query invocation."""

    query: str
    mode: str
    active_project: str | None
    route_candidates: list[RouteCandidate]
    selected_evidence_ids: list[str]
    dropped_evidence_ids: list[str]
    dropped_reasons: dict[str, str]
    token_counts: dict[str, int]
    fallback_used: bool
    routing_disabled: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "active_project": self.active_project,
            "route_candidates": [c.to_dict() for c in self.route_candidates],
            "selected_evidence_ids": self.selected_evidence_ids,
            "dropped_evidence_ids": self.dropped_evidence_ids,
            "dropped_reasons": self.dropped_reasons,
            "token_counts": self.token_counts,
            "fallback_used": self.fallback_used,
            "routing_disabled": self.routing_disabled,
            "error": self.error,
        }
