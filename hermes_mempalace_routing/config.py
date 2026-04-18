from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


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
