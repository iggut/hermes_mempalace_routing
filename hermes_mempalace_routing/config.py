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

    @classmethod
    def default(cls) -> "RoutingConfig":
        return cls(base_dir=Path(os.path.expanduser("~/.hermes/mempalace-routing")))

    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path.expanduser()
        return (self.base_dir.expanduser() / "metadata.db").resolve()

    def validate(self) -> None:
        if self.inject_top_k_routes < 0 or self.inject_top_k_raw_excerpts < 0:
            raise ValueError("inject_top_k_* must be non-negative")
        if self.storage_backend not in ("sqlite", "jsonl"):
            raise ValueError(f"Unknown storage_backend: {self.storage_backend}")
        if self.tokenizer_strategy not in ("auto", "tiktoken", "estimate"):
            raise ValueError(f"Unknown tokenizer_strategy: {self.tokenizer_strategy}")
