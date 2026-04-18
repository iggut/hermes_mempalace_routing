from hermes_mempalace_routing.context_engine import RoutingContextEngine
from hermes_mempalace_routing.models import MemoryEnvelope
from hermes_mempalace_routing.routing import RouteScorer


def test_allocate_budget():
    engine = RoutingContextEngine(RouteScorer())
    budget = engine.allocate_budget(10000)
    assert budget.live_conversation == 2000
    assert budget.routed_memory == 3500
    assert budget.raw_diagnostics == 1500
    assert budget.reserve == 1000


def test_select_evidence_prefers_project_and_stacktrace():
    engine = RoutingContextEngine(RouteScorer())
    envelopes = [
        MemoryEnvelope(
            memory_id="mem1",
            room="project/hermes",
            route_tags=["startup", "syntaxerror"],
            fact_type="stacktrace",
            summary="Hermes startup hit a SyntaxError in run_agent.py",
            provenance_artifact_ids=["art1"],
            provenance_excerpt="Traceback...SyntaxError",
            pinned=False,
        ),
        MemoryEnvelope(
            memory_id="mem2",
            room="project/orderking",
            route_tags=["flutter"],
            fact_type="note",
            summary="OrderKing uses ML Kit OCR fallback validation",
            provenance_artifact_ids=["art2"],
            pinned=False,
        ),
    ]
    selected = engine.select_evidence(
        query="why is hermes startup failing",
        envelopes=envelopes,
        active_project="project/hermes",
        mode="debugging",
        top_k=1,
    )
    assert len(selected) == 1
    assert selected[0].memory_id == "mem1"
