from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import RoutingConfig
from .conflicts import list_conflicts
from .context_engine import RoutingContextEngine
from .models import MemoryEnvelope, RouteRun
from .provider import MemPalaceRoutingProvider
from .routing import RouteScorer
from .storage import StorageBackend, StorageError, create_storage
from .tokenizer import count_tokens


class HermesMemPalaceRoutingPlugin:
    """Hermes integration surface: thin hooks delegate to routing and storage backends."""

    def __init__(self, config: RoutingConfig | None = None):
        self.config = config or RoutingConfig.default()
        self.config.validate()
        self.storage: StorageBackend = create_storage(self.config)
        self.provider = MemPalaceRoutingProvider(self.storage, self.config)
        self.context_engine = RoutingContextEngine(RouteScorer(self.config), self.config)

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
    ) -> MemoryEnvelope | None:
        """Persist a raw artifact plus envelope when enabled; otherwise no-op."""
        if not self.config.enabled or not self.config.write_raw_artifacts:
            return None
        try:
            return self.provider.store_artifact_as_memory(
                turn_id=turn_id,
                room=room,
                fact_type=fact_type,
                summary=summary,
                raw_text=raw_text,
                route_tags=route_tags,
                conflict_key=conflict_key,
                pinned=pinned,
                fail_open=self.config.fail_open_to_hermes_summarization,
            )
        except StorageError:
            if self.config.fail_open_to_hermes_summarization:
                return None
            raise

    def on_routing_failure(
        self,
        error: BaseException,
        *,
        query: str,
        total_tokens: int,
        active_project: str | None,
        mode: str,
    ) -> dict[str, Any]:
        """Hermes fallback payload when routing fails; keeps summarization path viable."""
        budget = self.context_engine.allocate_budget(total_tokens)
        trace = RouteRun(
            query=query,
            mode=mode,
            active_project=active_project,
            route_candidates=[],
            selected_evidence_ids=[],
            dropped_evidence_ids=[],
            dropped_reasons={},
            token_counts={
                "total_budget": total_tokens,
                "rendered_budget_tokens": 0,
            },
            fallback_used=True,
            routing_disabled=False,
            error=f"{type(error).__name__}: {error}",
        )
        self.storage.insert_route_run(trace)
        return {
            "budget": budget,
            "route_candidates": [],
            "evidence": [],
            "raw_diagnostic_excerpts": [],
            "rendered_block": "",
            "trace": trace,
            "fallback_used": True,
            "routing_disabled": False,
            "error": str(error),
        }

    def _build_context_inner(
        self,
        query: str,
        total_tokens: int,
        active_project: str | None,
        mode: str,
    ) -> dict[str, Any]:
        envelopes = self.storage.list_envelopes()
        conflicts = list_conflicts(
            envelopes,
            self.config,
            stored=self.storage.list_conflicts(),
        )
        budget = self.context_engine.allocate_budget(total_tokens)
        max_raw_chars = max(256, self.config.max_raw_excerpt_tokens * 4)

        evidence, ranked, dropped_evidence = self.context_engine.select_evidence(
            query=query,
            envelopes=envelopes,
            active_project=active_project,
            mode=mode,
            storage=self.storage,
            top_k=self.config.inject_top_k_routes,
            conflicts=conflicts,
            max_raw_chars_per_evidence=max_raw_chars,
        )
        cited = frozenset(
            pid for ev in evidence for pid in ev.provenance if ev.raw_excerpt
        )
        raw_excerpts = self.context_engine.select_raw_diagnostic_excerpts(
            query=query,
            envelopes=envelopes,
            active_project=active_project,
            mode=mode,
            storage=self.storage,
            top_k=self.config.inject_top_k_raw_excerpts,
            budget=budget,
            already_cited_artifact_ids=cited,
            conflicts=conflicts,
        )
        rendered = self.context_engine.render_injected_block(evidence, raw_excerpts)
        max_injection_tokens = budget.routed_memory + budget.raw_diagnostics
        fitted, budget_drops, ev_final, raw_final = self.context_engine.fit_to_token_budget(
            rendered, evidence, raw_excerpts, max_injection_tokens
        )
        dropped_all = {**dropped_evidence, **budget_drops}
        selected_ids = [e.memory_id for e in ev_final]
        dropped_reasons = dict(dropped_all)
        selected_set = set(selected_ids)
        for c in ranked:
            if c.memory_id not in selected_set and c.memory_id not in dropped_reasons:
                dropped_reasons[c.memory_id] = "below_top_k"

        tok_rendered = count_tokens(
            fitted,
            self.config.model_hint,
            self.config.provider_hint,
            strategy=self.config.tokenizer_strategy,
        )
        token_counts = {
            "total_budget": total_tokens,
            "routed_memory_budget": budget.routed_memory,
            "raw_diag_budget": budget.raw_diagnostics,
            "injection_token_cap": max_injection_tokens,
            "rendered_block_tokens_est": tok_rendered,
        }
        trace = RouteRun(
            query=query,
            mode=mode,
            active_project=active_project,
            route_candidates=ranked,
            selected_evidence_ids=selected_ids,
            dropped_evidence_ids=list(dropped_reasons.keys()),
            dropped_reasons=dropped_reasons,
            token_counts=token_counts,
            fallback_used=False,
            routing_disabled=False,
            error=None,
        )
        self.storage.insert_route_run(trace)
        return {
            "budget": budget,
            "route_candidates": ranked,
            "evidence": ev_final,
            "raw_diagnostic_excerpts": raw_final,
            "rendered_block": fitted,
            "trace": trace,
            "fallback_used": False,
            "routing_disabled": False,
            "error": None,
        }

    def _empty_disabled_payload(
        self,
        query: str,
        total_tokens: int,
        active_project: str | None,
        mode: str,
    ) -> dict[str, Any]:
        budget = self.context_engine.allocate_budget(total_tokens)
        trace = RouteRun(
            query=query,
            mode=mode,
            active_project=active_project,
            route_candidates=[],
            selected_evidence_ids=[],
            dropped_evidence_ids=[],
            dropped_reasons={},
            token_counts={"total_budget": total_tokens,
                "rendered_block_tokens_est": 0},
            fallback_used=False,
            routing_disabled=True,
            error=None,
        )
        return {
            "budget": budget,
            "route_candidates": [],
            "evidence": [],
            "raw_diagnostic_excerpts": [],
            "rendered_block": "",
            "trace": trace,
            "fallback_used": False,
            "routing_disabled": True,
            "error": None,
        }

    def build_context_for_query(
        self,
        query: str,
        total_tokens: int,
        active_project: str | None = None,
        mode: str = "debugging",
    ) -> dict[str, Any]:
        """Hermes pre-model hook: route-selected evidence, fail-open to summarization on error."""
        if not self.config.enabled:
            return self._empty_disabled_payload(query, total_tokens, active_project, mode)
        try:
            return self._build_context_inner(query, total_tokens, active_project, mode)
        except Exception as exc:
            if self.config.fail_open_to_hermes_summarization:
                return self.on_routing_failure(
                    exc,
                    query=query,
                    total_tokens=total_tokens,
                    active_project=active_project,
                    mode=mode,
                )
            raise

    @classmethod
    def from_base_dir(cls, base_dir: str | Path) -> "HermesMemPalaceRoutingPlugin":
        return cls(config=RoutingConfig(base_dir=Path(base_dir)))
