from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Literal

# MemPalace room taxonomy (extend with project/<name> for per-project loci).
STANDARD_ROOMS: frozenset[str] = frozenset(
    {"identity", "ops", "errors", "decisions", "scratch", "pinned"}
)


def project_room(name: str) -> str:
    """Return canonical `project/<name>` room id (lowercased segment for stable routing)."""
    n = name.strip().strip("/").lower()
    if n.startswith("project/"):
        return n
    return f"project/{n}"


StorageBackendName = Literal["sqlite", "jsonl"]
TokenizerStrategy = Literal["auto", "tiktoken", "estimate"]
TrimPolicy = Literal["deterministic_v1"]
RedactionPolicy = Literal["none", "mask", "drop"]
MemoryBackendName = Literal["local", "mempalace_first"]
WingStrategyName = Literal["active_project", "fixed"]
RoomStrategyName = Literal["fact_type_and_project", "project_only", "fixed_decisions"]

CONFLICT_PRECEDENCE_TERMS: frozenset[str] = frozenset({"runtime_truth", "pin", "newer_verified"})


def _default_room_weights_debugging() -> dict[str, float]:
    return {
        "identity": 1.0,
        "ops": 1.0,
        "errors": 1.15,
        "decisions": 1.1,
        "scratch": 0.85,
        "pinned": 1.2,
    }


def _default_room_weights_design() -> dict[str, float]:
    return {
        "identity": 1.0,
        "ops": 1.0,
        "errors": 0.95,
        "decisions": 1.25,
        "scratch": 0.9,
        "pinned": 1.2,
    }


@dataclass(slots=True)
class RoutingConfig:
    base_dir: Path
    storage_backend: StorageBackendName = "sqlite"
    db_path: Path | None = None
    enabled: bool = True
    fail_open_to_hermes_summarization: bool = True
    replace_hermes_summarization: bool = True
    write_raw_artifacts: bool = True
    redact_before_persist: bool = True
    redaction_policy: RedactionPolicy = "mask"
    tokenizer_strategy: TokenizerStrategy = "estimate"
    inject_top_k_routes: int = 4
    inject_top_k_raw_excerpts: int = 2
    retention_scratch_days: int | None = None
    retention_project_days: int | None = None
    room_weights_by_mode: dict[str, dict[str, float]] = field(default_factory=dict)
    conflict_precedence: list[str] = field(
        default_factory=lambda: ["runtime_truth", "pin", "newer_verified"]
    )
    dedupe_identical_raw: bool = True
    """Skip new envelopes when raw body SHA256 already exists (exact duplicate)."""
    dedupe_normalize_for_signature: bool = True
    """Normalize whitespace for stack/shell grouping signatures (exact line collapse)."""
    repeat_error_group_window: int = 10_000
    """Max recent envelopes scanned when grouping repeated stderr/stack fingerprints."""
    # Routing / scoring (Sprint 2)
    route_score_threshold: float = 0.0
    unresolved_conflict_penalty: float = 0.35
    verification_boost: float = 0.12
    recency_boost_max: float = 0.08
    recency_half_life_days: float = 30.0
    pin_boost: float = 0.25
    fact_type_bias: dict[str, float] = field(
        default_factory=lambda: {
            "stacktrace": 0.08,
            "shell_output": 0.06,
            "tool_output": 0.06,
            "note": 0.0,
        }
    )
    trim_policy: TrimPolicy = "deterministic_v1"
    max_provenance_tokens: int = 96
    max_raw_excerpt_tokens: int = 512
    tokenizer_fallback_safety_multiplier: float = 1.15
    model_hint: str | None = None
    provider_hint: str | None = None

    # Durable memory policy (Hermes host integrates MemPalace; routing stays local metadata + traces).
    memory_backend: MemoryBackendName = "local"
    """``local``: legacy local raw+envelope durable path. ``mempalace_first``: durable drawers via MemPalace."""
    mempalace_enabled: bool = False
    """When True and tools are wired, use :class:`~hermes_mempalace_routing.mempalace_adapter.MemPalaceAdapter`."""
    mempalace_fail_open: bool = True
    """If True, MemPalace errors do not abort chat (mirrors fail-open routing)."""
    mempalace_resume_on_start: bool = True
    """Session wake runs scoped MemPalace resume/search when hooks are used."""
    mempalace_recall_on_every_query: bool = True
    """Pre-model: search MemPalace for each query when ``mempalace_first`` (simplest past-fact coverage)."""
    mempalace_duplicate_threshold: float = 0.92
    mempalace_allow_duplicate_supersede: bool = False
    """If False, skip add_drawer when duplicate check matches."""
    mempalace_default_wing_strategy: WingStrategyName = "active_project"
    mempalace_default_room_strategy: RoomStrategyName = "fact_type_and_project"
    mempalace_include_legacy_local_envelopes: bool = False
    """When ``mempalace_first``, include pre-migration local envelopes (can double-count with MemPalace)."""
    mempalace_fallback_local_write: bool = False
    """If MemPalace tools are not wired, fall back to local raw+envelope writes (avoid if preventing double durable paths)."""
    disable_builtin_durable_memory: bool = True
    """Host hint: Hermes built-in durable blob path should be off; routing uses this for documentation/validation only."""

    @classmethod
    def default(cls) -> "RoutingConfig":
        return cls(base_dir=Path(os.path.expanduser("~/.hermes/mempalace-routing")))

    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path.expanduser().resolve()
        return (self.base_dir.expanduser().resolve() / "metadata.db").resolve()

    def room_weights_for_mode(self, mode: str) -> dict[str, float]:
        if self.room_weights_by_mode and mode in self.room_weights_by_mode:
            return dict(self.room_weights_by_mode[mode])
        if mode == "design":
            return _default_room_weights_design()
        return _default_room_weights_debugging()

    def validate(self) -> None:
        if self.inject_top_k_routes < 0 or self.inject_top_k_raw_excerpts < 0:
            raise ValueError("inject_top_k_* must be non-negative")
        if self.storage_backend not in ("sqlite", "jsonl"):
            raise ValueError(f"Unknown storage_backend: {self.storage_backend}")
        if self.tokenizer_strategy not in ("auto", "tiktoken", "estimate"):
            raise ValueError(f"Unknown tokenizer_strategy: {self.tokenizer_strategy}")
        if self.trim_policy not in ("deterministic_v1",):
            raise ValueError(f"Unknown trim_policy: {self.trim_policy}")
        base = self.base_dir.expanduser()
        if not str(base).strip():
            raise ValueError("base_dir must be non-empty")

        dbp = self.resolved_db_path()
        if dbp.exists() and dbp.is_dir():
            raise ValueError(f"db_path must be a file path, not a directory: {dbp}")
        parent = dbp.parent
        if self.storage_backend == "sqlite":
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"Cannot create database parent directory {parent}: {exc}") from exc
            if not (os.access(parent, os.W_OK) if parent.exists() else True):
                raise ValueError(f"Database parent directory not writable: {parent}")

        if self.route_score_threshold < 0.0 or self.route_score_threshold > 1.0:
            raise ValueError("route_score_threshold must be in [0, 1]")
        for name in ("unresolved_conflict_penalty", "verification_boost", "recency_boost_max"):
            v = getattr(self, name)
            if v < 0.0 or v > 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")
        if self.max_provenance_tokens < 0 or self.max_raw_excerpt_tokens < 0:
            raise ValueError("token limits must be non-negative")
        if self.tokenizer_fallback_safety_multiplier < 1.0:
            raise ValueError("tokenizer_fallback_safety_multiplier must be >= 1.0")

        # Contradictory / nonsensical combinations
        if self.redact_before_persist and not self.write_raw_artifacts:
            if not (self.memory_backend == "mempalace_first" and self.mempalace_enabled):
                raise ValueError(
                    "redact_before_persist requires write_raw_artifacts (nothing to redact otherwise)"
                )
        if self.redaction_policy not in ("none", "mask", "drop"):
            raise ValueError(f"Unknown redaction_policy: {self.redaction_policy}")
        if self.redaction_policy != "none" and not self.redact_before_persist:
            raise ValueError("redaction_policy requires redact_before_persist=True")

        seen_prec: set[str] = set()
        if not self.conflict_precedence:
            raise ValueError("conflict_precedence must be non-empty")
        for term in self.conflict_precedence:
            if term not in CONFLICT_PRECEDENCE_TERMS:
                raise ValueError(
                    f"Unknown conflict_precedence term {term!r}; "
                    f"allowed: {sorted(CONFLICT_PRECEDENCE_TERMS)}"
                )
            if term in seen_prec:
                raise ValueError(f"Duplicate conflict_precedence term: {term}")
            seen_prec.add(term)
        if seen_prec != CONFLICT_PRECEDENCE_TERMS:
            raise ValueError(
                "conflict_precedence must include each term exactly once: "
                f"{sorted(CONFLICT_PRECEDENCE_TERMS)}"
            )

        if self.repeat_error_group_window < 0 or self.repeat_error_group_window > 1_000_000:
            raise ValueError("repeat_error_group_window must be in [0, 1000000]")

        if self.memory_backend not in ("local", "mempalace_first"):
            raise ValueError(f"Unknown memory_backend: {self.memory_backend}")
        if self.mempalace_default_wing_strategy not in ("active_project", "fixed"):
            raise ValueError(f"Unknown mempalace_default_wing_strategy: {self.mempalace_default_wing_strategy}")
        if self.mempalace_default_room_strategy not in (
            "fact_type_and_project",
            "project_only",
            "fixed_decisions",
        ):
            raise ValueError(
                f"Unknown mempalace_default_room_strategy: {self.mempalace_default_room_strategy}"
            )
        if self.mempalace_duplicate_threshold < 0.0 or self.mempalace_duplicate_threshold > 1.0:
            raise ValueError("mempalace_duplicate_threshold must be in [0, 1]")
