from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import RoutingConfig
from .models import MemoryEnvelope
from .plugin import HermesMemPalaceRoutingPlugin


@dataclass(slots=True)
class HermesHostHooks:
    """Host-facing bridge that wires Hermes lifecycle events into the routing plugin.

    Hermes can call these methods directly at the two integration seams:
    - pre-model context assembly
    - post-turn artifact ingestion
    """

    plugin: HermesMemPalaceRoutingPlugin

    @classmethod
    def from_config(cls, config: RoutingConfig | None = None) -> "HermesHostHooks":
        return cls(HermesMemPalaceRoutingPlugin(config))

    def pre_model_context_assembly(
        self,
        query: str,
        total_tokens: int,
        active_project: str | None = None,
        mode: str = "debugging",
    ) -> dict[str, Any]:
        """Call the routing plugin at the pre-model seam."""
        return self.plugin.pre_model_context_assembly(
            query=query,
            total_tokens=total_tokens,
            active_project=active_project,
            mode=mode,
        )

    def post_turn_artifact_ingestion(
        self,
        turn_id: str,
        room: str,
        fact_type: str,
        summary: str,
        raw_text: str,
        route_tags: list[str] | None = None,
        conflict_key: str | None = None,
        pinned: bool = False,
    ) -> MemoryEnvelope | None:
        """Call the routing plugin at the post-turn artifact ingestion seam."""
        return self.plugin.post_turn_artifact_ingestion(
            turn_id=turn_id,
            room=room,
            fact_type=fact_type,
            summary=summary,
            raw_text=raw_text,
            route_tags=route_tags,
            conflict_key=conflict_key,
            pinned=pinned,
        )
