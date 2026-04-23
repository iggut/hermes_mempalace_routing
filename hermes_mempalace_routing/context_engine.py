from __future__ import annotations

from .config import RoutingConfig
from .models import (
    ConflictRecord,
    ConflictStatus,
    ContextBudget,
    InjectedEvidence,
    MemoryEnvelope,
    RawDiagnosticExcerpt,
    RouteCandidate,
)
import math
from datetime import UTC, datetime, timedelta

from .routing import RouteScorer, room_matches_active_project
from .storage import StorageBackend
from .tokenizer import count_tokens, truncate_to_tokens

_DIAGNOSTIC_FACTS = frozenset({"stacktrace", "shell_output", "tool_output"})

EXCLUSION_BELOW_SCORE_THRESHOLD = "below_score_threshold"
EXCLUSION_DROPPED_FOR_BUDGET = "dropped_for_budget"
EXCLUSION_CONFLICT_UNRESOLVED = "conflict_unresolved"
EXCLUSION_CONFLICT_LOSER = "conflict_loser_excluded"
EXCLUSION_MISSING_PROVENANCE = "missing_provenance"
EXCLUSION_REDACTION_BLOCKED = "redaction_blocked"
EXCLUSION_ARTIFACT_UNAVAILABLE = "artifact_unavailable"


class RoutingContextEngine:
    def __init__(self, scorer: RouteScorer, config: RoutingConfig | None = None):
        self.scorer = scorer
        self._config = config or RoutingConfig.default()

    def _tok(self, text: str) -> int:
        n = count_tokens(
            text,
            self._config.model_hint,
            self._config.provider_hint,
            strategy=self._config.tokenizer_strategy,
        )
        return int(n * self._config.tokenizer_fallback_safety_multiplier)

    def _precompute_scoring_params(
        self, query: str, conflicts: list[ConflictRecord] | None = None
    ) -> tuple[list[str], datetime, float, set[str], set[str]]:
        q_tokens = [t for t in query.lower().split() if t]
        now = datetime.now(UTC)
        hl = timedelta(days=self._config.recency_half_life_days).total_seconds()
        # Use -1.0 as a sentinel for hl <= 0 to avoid boost (original logic)
        lam = math.log(2) / hl if hl > 0 else -1.0

        losers: set[str] = set()
        unresolved: set[str] = set()
        if conflicts:
            for c in conflicts:
                if c.status == ConflictStatus.UNRESOLVED.value:
                    unresolved.update(c.candidate_memory_ids)
                else:
                    if c.loser_memory_ids:
                        losers.update(c.loser_memory_ids)

        return q_tokens, now, lam, losers, unresolved

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

    def select_route_candidates(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        conflicts: list[ConflictRecord],
    ) -> tuple[list[RouteCandidate], dict[str, str]]:
        """Rank all envelopes and record exclusion reasons for ineligible candidates."""
        q_tokens, now, lam, losers, unresolved = self._precompute_scoring_params(query, conflicts)

        scored: list[RouteCandidate] = [
            self.scorer.score(
                query,
                env,
                active_project,
                mode,
                conflicts=conflicts,
                precomputed_query_tokens=q_tokens,
                now=now,
                recency_lam=lam,
                precomputed_conflict_losers=losers,
                precomputed_unresolved_conflicts=unresolved,
            )
            for env in envelopes
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        dropped: dict[str, str] = {}
        thr = self._config.route_score_threshold
        for c in scored:
            if "missing_provenance" in c.rationale:
                dropped[c.memory_id] = EXCLUSION_MISSING_PROVENANCE
            elif "conflict_loser_excluded" in c.rationale:
                dropped[c.memory_id] = EXCLUSION_CONFLICT_LOSER
            elif c.score < thr:
                if "unresolved_conflict_penalty" in c.rationale:
                    dropped[c.memory_id] = EXCLUSION_CONFLICT_UNRESOLVED
                else:
                    dropped[c.memory_id] = EXCLUSION_BELOW_SCORE_THRESHOLD
        return scored, dropped

    def select_evidence(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        storage: StorageBackend,
        top_k: int,
        conflicts: list[ConflictRecord],
        max_raw_chars_per_evidence: int,
    ) -> tuple[list[InjectedEvidence], list[RouteCandidate], dict[str, str]]:
        ranked, dropped = self.select_route_candidates(query, envelopes, active_project, mode, conflicts)
        by_id = {env.memory_id: env for env in envelopes}
        eligible = [c for c in ranked if c.memory_id not in dropped]
        if active_project and mode == "design":
            matching = [c for c in eligible if room_matches_active_project(c.room, active_project)]
            if matching:
                eligible = matching
        selected: list[InjectedEvidence] = []
        extra_drops = dict(dropped)
        for candidate in eligible:
            if len(selected) >= max(0, top_k):
                break
            env = by_id[candidate.memory_id]
            raw_excerpt: str | None = None
            if env.fact_type in _DIAGNOSTIC_FACTS and env.provenance_artifact_ids:
                aid = env.provenance_artifact_ids[0]
                full = storage.read_artifact_text(aid)
                if full is None:
                    extra_drops[env.memory_id] = EXCLUSION_ARTIFACT_UNAVAILABLE
                    continue
                raw_excerpt = full[:max_raw_chars_per_evidence]
            selected.append(
                InjectedEvidence(
                    memory_id=env.memory_id,
                    room=env.room,
                    summary=env.summary,
                    provenance=list(env.provenance_artifact_ids),
                    raw_excerpt=raw_excerpt,
                    source_score=candidate.score,
                    confidence=env.confidence,
                    pinned=env.pinned,
                )
            )
        return selected, ranked, extra_drops

    def select_raw_diagnostic_excerpts(
        self,
        query: str,
        envelopes: list[MemoryEnvelope],
        active_project: str | None,
        mode: str,
        storage: StorageBackend,
        top_k: int,
        budget: ContextBudget | None = None,
        already_cited_artifact_ids: frozenset[str] | None = None,
        conflicts: list[ConflictRecord] | None = None,
    ) -> list[RawDiagnosticExcerpt]:
        conflicts = conflicts or []
        already_cited_artifact_ids = already_cited_artifact_ids or frozenset()
        diagnostic_envs = [e for e in envelopes if e.fact_type in _DIAGNOSTIC_FACTS and e.provenance_artifact_ids]

        q_tokens, now, lam, losers, unresolved = self._precompute_scoring_params(query, conflicts)

        scored = [
            self.scorer.score(
                query,
                env,
                active_project,
                mode,
                conflicts=conflicts,
                precomputed_query_tokens=q_tokens,
                now=now,
                recency_lam=lam,
                precomputed_conflict_losers=losers,
                precomputed_unresolved_conflicts=unresolved,
            )
            for env in diagnostic_envs
        ]
        scored.sort(key=lambda c: c.score, reverse=True)

        max_total_tokens = 4096
        if budget is not None:
            max_total_tokens = budget.raw_diagnostics
        per = max(64, max_total_tokens // max(top_k, 1))

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
            text = full
            if self._tok(text) > per:
                text = truncate_to_tokens(
                    full,
                    per,
                    self._config.model_hint,
                    self._config.provider_hint,
                    strategy=self._config.tokenizer_strategy,
                )
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
        *,
        cap_raw_excerpt_tokens: int | None = None,
        cap_provenance_tokens: int | None = None,
    ) -> str:
        cap_raw = cap_raw_excerpt_tokens if cap_raw_excerpt_tokens is not None else self._config.max_raw_excerpt_tokens
        cap_prov = cap_provenance_tokens if cap_provenance_tokens is not None else self._config.max_provenance_tokens
        lines = ["[MemPalace routed evidence]"]
        if not evidence:
            lines.append("- no routed evidence selected")
        else:
            for idx, item in enumerate(evidence, start=1):
                lines.append(f"{idx}. room={item.room}")
                lines.append(f"   summary={item.summary}")
                prov = ", ".join(item.provenance)
                if self._tok(prov) > cap_prov:
                    prov = truncate_to_tokens(
                        prov,
                        cap_prov,
                        self._config.model_hint,
                        self._config.provider_hint,
                        strategy=self._config.tokenizer_strategy,
                    )
                lines.append(f"   provenance={prov}")
                if item.raw_excerpt:
                    ex = item.raw_excerpt
                    if self._tok(ex) > cap_raw:
                        ex = truncate_to_tokens(
                            ex,
                            cap_raw,
                            self._config.model_hint,
                            self._config.provider_hint,
                            strategy=self._config.tokenizer_strategy,
                        )
                    lines.append(f"   raw_excerpt={ex}")

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

    def fit_to_token_budget(
        self,
        rendered: str,
        evidence: list[InjectedEvidence],
        raw_diagnostic_excerpts: list[RawDiagnosticExcerpt],
        max_tokens: int,
    ) -> tuple[str, dict[str, str], list[InjectedEvidence], list[RawDiagnosticExcerpt]]:
        """
        Enforce hard token cap with deterministic trimming: drop whole evidence items first
        (scratch / low confidence / low score), shorten raw diagnostics, then provenance, then global truncate.
        """
        drops: dict[str, str] = {}
        ev = list(evidence)
        raw = list(raw_diagnostic_excerpts)

        def total_render() -> str:
            return self.render_injected_block(ev, raw)

        if self._tok(rendered) <= max_tokens:
            return rendered, drops, ev, raw

        def removal_sort_key(item: InjectedEvidence) -> tuple[int, float, float, str]:
            is_scratch = "scratch" in item.room.lower()
            if is_scratch:
                return (0, item.source_score, item.confidence, item.memory_id)
            pin_tier = 1.0 if item.pinned else 0.0
            return (1, pin_tier, item.confidence, item.memory_id)

        guard = 0
        while self._tok(total_render()) > max_tokens and guard < 256:
            guard += 1
            if ev:
                ev_sorted = sorted(ev, key=removal_sort_key)
                victim = ev_sorted[0]
                ev = [e for e in ev if e.memory_id != victim.memory_id]
                drops[victim.memory_id] = EXCLUSION_DROPPED_FOR_BUDGET
                continue
            if raw:
                raw.pop()
                continue
            break

        rendered2 = total_render()
        if self._tok(rendered2) > max_tokens:
            rendered2 = truncate_to_tokens(
                rendered2,
                max_tokens,
                self._config.model_hint,
                self._config.provider_hint,
                strategy=self._config.tokenizer_strategy,
            )
        return rendered2, drops, ev, raw
