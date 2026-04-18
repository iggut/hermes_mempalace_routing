from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.models import ConflictRecord, ConflictStatus, MemoryEnvelope
from hermes_mempalace_routing.routing import RouteScorer


def test_route_scorer_debugging_boosts_diagnostics():
    scorer = RouteScorer()
    env_diag = MemoryEnvelope(
        memory_id="m1",
        room="errors",
        fact_type="stacktrace",
        summary="traceback",
        route_tags=[],
        provenance_artifact_ids=["art_m1"],
    )
    env_note = MemoryEnvelope(
        memory_id="m2",
        room="scratch",
        fact_type="note",
        summary="traceback",
        route_tags=[],
        provenance_artifact_ids=["art_m2"],
    )
    r1 = scorer.score("traceback", env_diag, active_project=None, mode="debugging")
    r2 = scorer.score("traceback", env_note, active_project=None, mode="debugging")
    assert r1.score > r2.score
    assert "debugging_diagnostic_boost" in r1.rationale


def test_route_scorer_active_project_bias():
    scorer = RouteScorer()
    env = MemoryEnvelope(
        memory_id="m1",
        room="project/hermes",
        fact_type="note",
        summary="build failure",
        route_tags=["ci"],
        provenance_artifact_ids=["art_m1"],
    )
    r = scorer.score("build failure", env, active_project="hermes", mode="design")
    assert "active_project_room" in r.rationale


def test_route_scorer_pinned_boost():
    scorer = RouteScorer()
    pinned = MemoryEnvelope(
        memory_id="m1",
        room="scratch",
        summary="x",
        pinned=True,
        provenance_artifact_ids=["art_p"],
    )
    unpinned = MemoryEnvelope(
        memory_id="m2",
        room="scratch",
        summary="x",
        pinned=False,
        provenance_artifact_ids=["art_u"],
    )
    rp = scorer.score("x", pinned, None, "debugging")
    ru = scorer.score("x", unpinned, None, "debugging")
    assert rp.score > ru.score
    assert "pinned" in rp.rationale


def test_score_breakdown_is_structured():
    cfg = RoutingConfig.default()
    scorer = RouteScorer(cfg)
    env = MemoryEnvelope(
        memory_id="m1",
        room="project/hermes",
        fact_type="note",
        summary="build",
        route_tags=[],
        provenance_artifact_ids=["art1"],
    )
    r = scorer.score("build", env, active_project="hermes", mode="debugging")
    assert r.score_breakdown
    assert "active_project" in r.score_breakdown


def test_unresolved_conflict_penalty_in_breakdown():
    cfg = RoutingConfig.default()
    scorer = RouteScorer(cfg)
    env = MemoryEnvelope(
        memory_id="m1",
        room="scratch",
        summary="x",
        provenance_artifact_ids=["a"],
        conflict_key="ck",
        pinned=False,
    )
    conflicts = [
        ConflictRecord(
            conflict_key="ck",
            room="scratch",
            candidate_memory_ids=["m1", "m2"],
            status=ConflictStatus.UNRESOLVED.value,
        )
    ]
    r = scorer.score("x", env, conflicts=conflicts)
    assert "unresolved_conflict_penalty" in r.score_breakdown


def test_design_mode_differs_from_debugging_for_note():
    cfg = RoutingConfig.default()
    scorer = RouteScorer(cfg)
    env = MemoryEnvelope(
        memory_id="m1",
        room="scratch",
        fact_type="note",
        summary="hello world",
        route_tags=[],
        provenance_artifact_ids=["a"],
    )
    rd = scorer.score("hello", env, None, "debugging")
    rs = scorer.score("hello", env, None, "design")
    assert "design_mode_bias" in rs.rationale
    assert "debugging_diagnostic_boost" not in rs.rationale
    assert rd.score != rs.score
