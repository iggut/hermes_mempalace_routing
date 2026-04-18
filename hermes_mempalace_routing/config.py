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
    redact_before_persist: bool = False
    tokenizer_strategy: TokenizerStrategy = "estimate"
    inject_top_k_routes: int = 4
    inject_top_k_raw_excerpts: int = 2
    retention_scratch_days: int | None = None
    retention_project_days: int | None = None
    room_weights_by_mode: dict[str, dict[str, float]] = field(default_factory=dict)
    conflict_precedence: list[str] = field(
        default_factory=lambda: ["pin", "runtime_truth", "newer_verified"]
    )
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
            raise ValueError(
                "redact_before_persist requires write_raw_artifacts (nothing to redact otherwise)"
            )
