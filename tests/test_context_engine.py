from pathlib import Path

from hermes_mempalace_routing.context_engine import RoutingContextEngine
from hermes_mempalace_routing.models import MemoryEnvelope
from hermes_mempalace_routing.routing import RouteScorer
from hermes_mempalace_routing.storage import RoutingStorage


def test_allocate_budget():
    engine = RoutingContextEngine(RouteScorer())
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
    engine = RoutingContextEngine(RouteScorer())
    evidence, ranked = engine.select_evidence(
        query="why is hermes startup failing",
        envelopes=envelopes,
        active_project="project/hermes",
        mode="debugging",
        storage=storage,
        top_k=1,
    )
    assert len(evidence) == 1
    assert evidence[0].memory_id == "mem1"
    assert evidence[0].raw_excerpt is not None
    assert "SyntaxError" in evidence[0].raw_excerpt
    assert ranked[0].memory_id == "mem1"
