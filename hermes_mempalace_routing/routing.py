from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .config import RoutingConfig, project_room
from .models import ConflictRecord, ConflictStatus, MemoryEnvelope, RouteCandidate, VerificationStatus

_DIAGNOSTIC_FACTS = frozenset({"stacktrace", "shell_output", "tool_output"})


def _normalize_active_project(active_project: str | None) -> str | None:
    if not active_project:
        return None
    s = active_project.strip()
    if not s:
        return None
    return project_room(s)


def _active_project_matches_room(room: str, active_project: str | None) -> bool:
    ap = _normalize_active_project(active_project)
    if not ap:
        return False
    rl = room.lower().strip()
    return rl == ap or rl.startswith(ap + "/")


def _base_room_weight(room: str, weights: dict[str, float]) -> float:
    rl = room.lower()
    if rl.startswith("project/"):
        return weights.get("decisions", 1.0)
    for key, w in weights.items():
        if key != "project" and key in rl:
            return w
    for std in ("identity", "ops", "errors", "decisions", "scratch", "pinned"):
        if std in rl or rl == std:
            return weights.get(std, 1.0)
    return 1.0


def _parse_created_at(created_at: str) -> datetime | None:
    if not created_at:
        return None
    try:
        s = created_at.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _recency_factor(created_at: str, half_life_days: float, max_boost: float) -> float:
    dt = _parse_created_at(created_at)
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    age = datetime.now(UTC) - dt
    if age.total_seconds() < 0:
        return max_boost
    hl = timedelta(days=half_life_days).total_seconds()
    if hl <= 0:
        return 0.0
    import math

    lam = math.log(2) / hl
    return max_boost * math.exp(-lam * age.total_seconds())


class RouteScorer:
    def __init__(self, config: RoutingConfig | None = None):
        self._config = config or RoutingConfig.default()

    def score(
        self,
        query: str,
        env: MemoryEnvelope,
        active_project: str | None = None,
        mode: str = "debugging",
        *,
        conflicts: list[ConflictRecord] | None = None,
    ) -> RouteCandidate:
        conflicts = conflicts or []
        bd: dict[str, float] = {}
        rationale: list[str] = []

        if not env.provenance_artifact_ids:
            return RouteCandidate(
                room=env.room,
                memory_id=env.memory_id,
                score=0.0,
                rationale=["missing_provenance"],
                score_breakdown={},
            )

        summary = env.summary.lower()
        route_tags = [tag.lower() for tag in env.route_tags]
        q_tokens = [t for t in query.lower().split() if t]

        weights = self._config.room_weights_for_mode(mode)

        if _active_project_matches_room(env.room, active_project):
            bd["active_project"] = 0.30
            rationale.append("active_project_room")

        bd["pin"] = self._config.pin_boost if env.pinned else 0.0
        if env.pinned:
            rationale.append("pinned")

        rw = _base_room_weight(env.room, weights)
        bd["room_weight"] = (rw - 1.0) * 0.15
        if bd["room_weight"] != 0.0:
            rationale.append("room_weight")

        if mode == "debugging" and env.fact_type in _DIAGNOSTIC_FACTS:
            bd["debugging_diagnostic"] = 0.25
            rationale.append("debugging_diagnostic_boost")
        elif mode == "design" and env.fact_type not in _DIAGNOSTIC_FACTS:
            bd["design_non_diagnostic"] = 0.08
            rationale.append("design_mode_bias")

        if any(token and token in summary for token in q_tokens):
            bd["summary_overlap"] = 0.20
            rationale.append("summary_term_overlap")

        if any(token and any(token in tag for tag in route_tags) for token in q_tokens):
            bd["route_tag_overlap"] = 0.10
            rationale.append("route_tag_overlap")

        ft = self._config.fact_type_bias.get(env.fact_type, 0.0)
        if ft:
            bd["fact_type_bias"] = ft
            rationale.append("fact_type_bias")

        if env.room.lower() in ("errors",) or "errors" in env.room.lower() or env.fact_type == "stacktrace":
            bd["errors_bias"] = 0.10
            rationale.append("errors_room_or_stacktrace_bias")

        if env.verification_status == VerificationStatus.VERIFIED.value:
            bd["verification"] = self._config.verification_boost
            rationale.append("verified")

        bd["recency"] = _recency_factor(
            env.created_at,
            self._config.recency_half_life_days,
            self._config.recency_boost_max,
        )
        if bd["recency"] > 0.001:
            rationale.append("recency")

        raw = sum(bd.values())
        if self._memory_in_unresolved_conflict(env, conflicts) and not env.pinned:
            pen = self._config.unresolved_conflict_penalty
            bd["unresolved_conflict_penalty"] = -pen
            raw -= pen
            rationale.append("unresolved_conflict_penalty")

        score = max(0.0, min(1.0, raw))

        return RouteCandidate(
            room=env.room,
            memory_id=env.memory_id,
            score=score,
            rationale=rationale,
            score_breakdown=bd,
        )

    @staticmethod
    def _memory_in_unresolved_conflict(env: MemoryEnvelope, conflicts: list[ConflictRecord]) -> bool:
        for c in conflicts:
            if c.status != ConflictStatus.UNRESOLVED.value:
                continue
            if env.memory_id in c.candidate_memory_ids:
                return True
        return False
