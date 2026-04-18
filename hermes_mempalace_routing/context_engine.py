from __future__ import annotations

from .models import (
    ContextBudget,
    InjectedEvidence,
    MemoryEnvelope,
    RawDiagnosticExcerpt,
    RouteCandidate,
)
from .routing import RouteScorer
from .storage import StorageBackend

_DIAGNOSTIC_FACTS = frozenset({"stacktrace", "shell_output", "tool_output"})


def _approx_chars_for_tokens(tokens: int) -> int:
    return max(0, tokens * 4)


class RoutingContextEngine:
    def __init__(self, scorer: RouteScorer):
        self.scorer = scorer

    def allocate_budget(self, total_tokens: int) -> ContextBudget:
        live = int(total_tokens * 0.20)
        routed = int(total_tokens * 0.35)
        raw_diag = int(total_tokens * 0.15)
        reserve = int(total_tokens * 0.10)
        used = live + routed + raw_diag + reserve
        remainder = max(0, total_tokens - used)
        return ContextBudget(
            total_tokens=total_tokens,
            live_conversation=live,
            routed_memory=routed,
            raw_diagnostics=raw_diag,
            reserve=reserve,
            remainder=remainder,
        )

    def rank_candidates(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
    ) -> list[RouteCandidate]:
        scored = [
            self.scorer.score(query=query, env=env, active_project=active_project, mode=mode)
            for env in envelopes
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def select_evidence(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        storage: StorageBackend,
        top_k: int = 4,
        max_raw_chars_per_evidence: int = 2000,
    ) -> tuple[list[InjectedEvidence], list[RouteCandidate]]:
        scored = self.rank_candidates(query, envelopes, active_project, mode)
        by_id = {env.memory_id: env for env in envelopes}
        selected: list[InjectedEvidence] = []
        for candidate in scored[:top_k]:
            env = by_id[candidate.memory_id]
            raw_excerpt: str | None = None
            if env.fact_type in _DIAGNOSTIC_FACTS and env.provenance_artifact_ids:
                aid = env.provenance_artifact_ids[0]
                full = storage.read_artifact_text(aid)
                if full is not None:
                    raw_excerpt = full[:max_raw_chars_per_evidence]
            selected.append(
                InjectedEvidence(
                    memory_id=env.memory_id,
                    room=env.room,
                    summary=env.summary,
                    provenance=list(env.provenance_artifact_ids),
                    raw_excerpt=raw_excerpt,
                )
            )
        return selected, scored

    def select_raw_diagnostic_excerpts(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        storage: StorageBackend,
        top_k: int = 2,
        budget: ContextBudget | None = None,
        already_cited_artifact_ids: frozenset[str] | None = None,
    ) -> list[RawDiagnosticExcerpt]:
        """Top-K diagnostic memories by route score; exact text from artifact files (capped for prompt)."""
        already_cited_artifact_ids = already_cited_artifact_ids or frozenset()
        diagnostic_envs = [e for e in envelopes if e.fact_type in _DIAGNOSTIC_FACTS and e.provenance_artifact_ids]
        scored = [
            self.scorer.score(query=query, env=env, active_project=active_project, mode=mode)
            for env in diagnostic_envs
        ]
        scored.sort(key=lambda item: item.score, reverse=True)

        max_total_chars = 16_384
        if budget is not None:
            max_total_chars = _approx_chars_for_tokens(budget.raw_diagnostics)
        per = max(256, max_total_chars // max(top_k, 1))

        by_id = {env.memory_id: env for env in envelopes}
        out: list[RawDiagnosticExcerpt] = []
        seen_art: set[str] = set()
        for cand in scored:
            if len(out) >= top_k:
                break
            env = by_id[cand.memory_id]
            aid = env.provenance_artifact_ids[0]
            if aid in already_cited_artifact_ids or aid in seen_art:
                continue
            seen_art.add(aid)
            full = storage.read_artifact_text(aid)
            if full is None:
                continue
            text = full[:per]
            out.append(
                RawDiagnosticExcerpt(
                    artifact_id=aid,
                    memory_id=env.memory_id,
                    room=env.room,
                    text=text,
                )
            )
        return out

    def render_injected_block(
        self,
        evidence: list[InjectedEvidence],
        raw_diagnostic_excerpts: list[RawDiagnosticExcerpt] | None = None,
    ) -> str:
        lines = ["[MemPalace routed evidence]"]
        if not evidence:
            lines.append("- no routed evidence selected")
        else:
            for idx, item in enumerate(evidence, start=1):
                lines.append(f"{idx}. room={item.room}")
                lines.append(f"   summary={item.summary}")
                lines.append(f"   provenance={', '.join(item.provenance)}")
                if item.raw_excerpt:
                    # Prompt assembly: optional truncation only on outbound path (not at storage).
                    excerpt = item.raw_excerpt
                    cap = 400
                    if len(excerpt) > cap:
                        excerpt = excerpt[:cap] + "…"
                    lines.append(f"   raw_excerpt={excerpt}")

        raw_diagnostic_excerpts = raw_diagnostic_excerpts or []
        lines.append("")
        lines.append("[MemPalace raw diagnostics (exact excerpts, prompt-capped)]")
        if not raw_diagnostic_excerpts:
            lines.append("- no additional raw diagnostic excerpts")
        else:
            for idx, chunk in enumerate(raw_diagnostic_excerpts, start=1):
                lines.append(f"{idx}. artifact_id={chunk.artifact_id} memory_id={chunk.memory_id} room={chunk.room}")
                lines.append(chunk.text)

        return "\n".join(lines)
