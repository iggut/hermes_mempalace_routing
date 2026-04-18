from __future__ import annotations

from .models import ContextBudget, InjectedEvidence, MemoryEnvelope
from .routing import RouteScorer


class RoutingContextEngine:
    def __init__(self, scorer: RouteScorer):
        self.scorer = scorer

    def allocate_budget(self, total_tokens: int) -> ContextBudget:
        return ContextBudget(
            total_tokens=total_tokens,
            live_conversation=int(total_tokens * 0.20),
            routed_memory=int(total_tokens * 0.35),
            raw_diagnostics=int(total_tokens * 0.15),
            reserve=int(total_tokens * 0.10),
        )

    def select_evidence(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        top_k: int = 4,
    ) -> list[InjectedEvidence]:
        scored = [
            self.scorer.score(query=query, env=env, active_project=active_project, mode=mode)
            for env in envelopes
        ]
        scored.sort(key=lambda item: item.score, reverse=True)

        by_id = {env.memory_id: env for env in envelopes}
        selected: list[InjectedEvidence] = []
        for candidate in scored[:top_k]:
            env = by_id[candidate.memory_id]
            selected.append(
                InjectedEvidence(
                    memory_id=env.memory_id,
                    room=env.room,
                    summary=env.summary,
                    provenance=env.provenance_artifact_ids,
                    raw_excerpt=env.provenance_excerpt if env.fact_type in {"stacktrace", "shell_output", "tool_output"} else None,
                )
            )
        return selected

    def render_injected_block(self, evidence: list[InjectedEvidence]) -> str:
        lines = ["[MemPalace routed evidence]"]
        if not evidence:
            lines.append("- no routed evidence selected")
            return "\n".join(lines)

        for idx, item in enumerate(evidence, start=1):
            lines.append(f"{idx}. room={item.room}")
            lines.append(f"   summary={item.summary}")
            lines.append(f"   provenance={', '.join(item.provenance)}")
            if item.raw_excerpt:
                excerpt = item.raw_excerpt.replace("\n", " ").strip()
                lines.append(f"   raw_excerpt={excerpt[:220]}")
        return "\n".join(lines)
