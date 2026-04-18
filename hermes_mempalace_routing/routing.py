from __future__ import annotations

from .models import MemoryEnvelope, RouteCandidate


class RouteScorer:
    def score(
        self,
        query: str,
        env: MemoryEnvelope,
        active_project: str | None = None,
        mode: str = "debugging",
    ) -> RouteCandidate:
        q = query.lower()
        room = env.room.lower()
        summary = env.summary.lower()
        route_tags = [tag.lower() for tag in env.route_tags]

        score = 0.0
        rationale: list[str] = []

        if active_project and active_project.lower() in room:
            score += 0.30
            rationale.append("active_project_match")

        if env.pinned:
            score += 0.25
            rationale.append("pinned")

        if mode == "debugging" and env.fact_type in {"stacktrace", "shell_output", "tool_output"}:
            score += 0.25
            rationale.append("raw_diagnostic_bonus")

        if any(token and token in summary for token in q.split()):
            score += 0.20
            rationale.append("summary_term_overlap")

        if any(token and any(token in tag for tag in route_tags) for token in q.split()):
            score += 0.10
            rationale.append("route_tag_overlap")

        if "error" in room or env.fact_type == "stacktrace":
            score += 0.10
            rationale.append("error_room_bias")

        return RouteCandidate(room=env.room, memory_id=env.memory_id, score=min(score, 1.0), rationale=rationale)
