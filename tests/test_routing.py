from hermes_mempalace_routing.models import MemoryEnvelope
from hermes_mempalace_routing.routing import RouteScorer


def test_route_scorer_debugging_boosts_diagnostics():
    scorer = RouteScorer()
    env_diag = MemoryEnvelope(
        memory_id="m1",
        room="errors",
        fact_type="stacktrace",
        summary="traceback",
        route_tags=[],
    )
    env_note = MemoryEnvelope(
        memory_id="m2",
        room="scratch",
        fact_type="note",
        summary="traceback",
        route_tags=[],
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
    )
    unpinned = MemoryEnvelope(
        memory_id="m2",
        room="scratch",
        summary="x",
        pinned=False,
    )
    rp = scorer.score("x", pinned, None, "debugging")
    ru = scorer.score("x", unpinned, None, "debugging")
    assert rp.score > ru.score
    assert "pinned" in rp.rationale
