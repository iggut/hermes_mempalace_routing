from __future__ import annotations

from pathlib import Path

from .config import RoutingConfig
from .context_engine import RoutingContextEngine
from .provider import MemPalaceRoutingProvider
from .routing import RouteScorer
from .storage import RoutingStorage


class HermesMemPalaceRoutingPlugin:
    def __init__(self, config: RoutingConfig | None = None):
        self.config = config or RoutingConfig.default()
        self.storage = RoutingStorage(self.config.base_dir)
        self.provider = MemPalaceRoutingProvider(self.storage)
        self.context_engine = RoutingContextEngine(RouteScorer())

    def record_turn_artifact(
        self,
        turn_id: str,
        room: str,
        fact_type: str,
        summary: str,
        raw_text: str,
        route_tags: list[str] | None = None,
        conflict_key: str | None = None,
        pinned: bool = False,
    ):
        """Persist an exact raw artifact plus a routing envelope.

        TODO(Hermes): call this after user turns, assistant replies, tool output,
        shell output, and stack traces are produced.
        """
        return self.provider.store_artifact_as_memory(
            turn_id=turn_id,
            room=room,
            fact_type=fact_type,
            summary=summary,
            raw_text=raw_text,
            route_tags=route_tags,
            conflict_key=conflict_key,
            pinned=pinned,
        )

    def build_context_for_query(
        self,
        query: str,
        total_tokens: int,
        active_project: str | None = None,
        mode: str = "debugging",
    ) -> dict:
        """Return route-selected evidence for prompt assembly.

        TODO(Hermes): call this before any summarization fallback. Replace generic
        context compression for the working context path with this route-aware selection.
        """
        envelopes = self.storage.list_envelopes()
        budget = self.context_engine.allocate_budget(total_tokens)
        evidence = self.context_engine.select_evidence(
            query=query,
            envelopes=envelopes,
            active_project=active_project,
            mode=mode,
            top_k=self.config.inject_top_k_routes,
        )
        return {
            "budget": budget,
            "evidence": evidence,
            "rendered_block": self.context_engine.render_injected_block(evidence),
        }

    @classmethod
    def from_base_dir(cls, base_dir: str | Path) -> "HermesMemPalaceRoutingPlugin":
        return cls(config=RoutingConfig(base_dir=Path(base_dir)))
