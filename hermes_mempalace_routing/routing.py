from __future__ import annotations

from .config import project_room
from .models import MemoryEnvelope, RouteCandidate

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


class RouteScorer:
    def score(
        self,
        query: str,
        env: MemoryEnvelope,
        active_project: str | None = None,
        mode: str = "debugging",
    ) -> RouteCandidate:
        summary = env.summary.lower()
        route_tags = [tag.lower() for tag in env.route_tags]

        score = 0.0
        rationale: list[str] = []

        if _active_project_matches_room(env.room, active_project):
            score += 0.30
            rationale.append("active_project_room")

        if env.pinned:
            score += 0.25
            rationale.append("pinned")

        if mode == "debugging" and env.fact_type in _DIAGNOSTIC_FACTS:
            score += 0.25
            rationale.append("debugging_diagnostic_boost")

        q_tokens = [t for t in query.lower().split() if t]
        if any(token and token in summary for token in q_tokens):
            score += 0.20
            rationale.append("summary_term_overlap")

        if any(token and any(token in tag for tag in route_tags) for token in q_tokens):
            score += 0.10
            rationale.append("route_tag_overlap")

        if "errors" in env.room.lower() or env.fact_type == "stacktrace":
            score += 0.10
            rationale.append("errors_room_or_stacktrace_bias")

        return RouteCandidate(room=env.room, memory_id=env.memory_id, score=min(score, 1.0), rationale=rationale)
