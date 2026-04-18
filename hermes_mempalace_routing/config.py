from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

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


@dataclass(slots=True)
class RoutingConfig:
    base_dir: Path
    enabled: bool = True
    replace_hermes_summarization: bool = True
    inject_top_k_routes: int = 4
    inject_top_k_raw_excerpts: int = 2

    @classmethod
    def default(cls) -> "RoutingConfig":
        return cls(base_dir=Path(os.path.expanduser("~/.hermes/mempalace-routing")))
