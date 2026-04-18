from pathlib import Path

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.context_engine import EXCLUSION_BELOW_SCORE_THRESHOLD, RoutingContextEngine
from hermes_mempalace_routing.models import InjectedEvidence, MemoryEnvelope
from hermes_mempalace_routing.routing import RouteScorer
from hermes_mempalace_routing.storage import RoutingStorage


def test_allocate_budget():
    from hermes_mempalace_routing.config import RoutingConfig

    engine = RoutingContextEngine(RouteScorer(RoutingConfig.default()), RoutingConfig.default())
    budget = engine.allocate_budget(10000)
    assert budget.live_conversation == 2000
    assert budget.routed_memory == 3500
    assert budget.raw_diagnostics == 1500
    assert budget.reserve == 1000
    assert budget.remainder == 10000 - (2000 + 3500 + 1500 + 1000)


def test_select_evidence_prefers_project_and_stacktrace(tmp_path: Path):
    base = tmp_path / "store"
    storage = RoutingStorage(base)
    r1 = storage.persist_raw_artifact("t1", "stacktrace", "SyntaxError: invalid syntax\n")
    r2 = storage.persist_raw_artifact("t1", "note", "other")

    envelopes = [
        MemoryEnvelope(
            memory_id="mem1",
            room="project/hermes",
            route_tags=["startup", "syntaxerror"],
            fact_type="stacktrace",
            summary="Hermes startup hit a SyntaxError in run_agent.py",
            provenance_artifact_ids=[r1.artifact_id],
            pinned=False,
        ),
        MemoryEnvelope(
            memory_id="mem2",
            room="project/orderking",
            route_tags=["flutter"],
            fact_type="note",
            summary="OrderKing uses ML Kit OCR fallback validation",
            provenance_artifact_ids=[r2.artifact_id],
            pinned=False,
        ),
    ]
    from hermes_mempalace_routing.config import RoutingConfig

    engine = RoutingContextEngine(RouteScorer(RoutingConfig.default()), RoutingConfig.default())
    evidence, ranked, _drops = engine.select_evidence(
        query="why is hermes startup failing",
        envelopes=envelopes,
        active_project="project/hermes",
        mode="debugging",
        storage=storage,
        top_k=1,
        conflicts=[],
        max_raw_chars_per_evidence=8000,
    )
    assert len(evidence) == 1
    assert evidence[0].memory_id == "mem1"
    assert evidence[0].raw_excerpt is not None
    assert "SyntaxError" in evidence[0].raw_excerpt
    assert ranked[0].memory_id == "mem1"


def test_select_route_candidates_respects_score_threshold():
    cfg = RoutingConfig.default()
    cfg.route_score_threshold = 1.0
    engine = RoutingContextEngine(RouteScorer(cfg), cfg)
    env = MemoryEnvelope(
        memory_id="low",
        room="scratch",
        summary="unlikely query match xyzabc",
        provenance_artifact_ids=["art1"],
    )
    ranked, dropped = engine.select_route_candidates("querynomatch12345", [env], None, "debugging", [])
    assert "low" in dropped
    assert dropped["low"] == EXCLUSION_BELOW_SCORE_THRESHOLD
    assert ranked


def test_fit_to_token_budget_never_exceeds_cap():
    cfg = RoutingConfig.default()
    engine = RoutingContextEngine(RouteScorer(cfg), cfg)
    ev = [
        InjectedEvidence(
            memory_id="m1",
            room="scratch",
            summary="S" * 4000,
            provenance=["p1"],
            source_score=0.2,
            confidence=0.2,
        ),
        InjectedEvidence(
            memory_id="m2",
            room="decisions",
            summary="T" * 4000,
            provenance=["p2"],
            source_score=0.9,
            confidence=0.9,
        ),
    ]
    rendered = engine.render_injected_block(ev, [])
    fitted, drops, _evf, _raw = engine.fit_to_token_budget(rendered, ev, [], max_tokens=120)
    assert engine._tok(fitted) <= 120
    assert drops
