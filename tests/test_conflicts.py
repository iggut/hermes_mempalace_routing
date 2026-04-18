from hermes_mempalace_routing.conflicts import ConflictResolver, choose_effective_memory, detect_conflicts
from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.models import ConflictRecord, ConflictStatus, MemoryEnvelope


def test_detect_conflicts_prefers_pinned():
    envelopes = [
        MemoryEnvelope(
            memory_id="mem1",
            room="project/hermes",
            summary="LM Studio is primary provider",
            conflict_key="provider.primary",
            pinned=True,
        ),
        MemoryEnvelope(
            memory_id="mem2",
            room="project/hermes",
            summary="llama.cpp is primary provider",
            conflict_key="provider.primary",
            pinned=False,
        ),
    ]
    conflicts = ConflictResolver().detect(envelopes)
    assert len(conflicts) == 1
    assert conflicts[0].resolved_memory_id == "mem1"
    assert conflicts[0].resolution_reason == "precedence_pin"


def test_runtime_truth_beats_pin_under_default_precedence() -> None:
    cfg = RoutingConfig.default()
    group = [
        MemoryEnvelope(
            memory_id="m1",
            room="project/x",
            summary="pinned only",
            conflict_key="k",
            pinned=True,
            route_tags=[],
        ),
        MemoryEnvelope(
            memory_id="m2",
            room="project/x",
            summary="runtime",
            conflict_key="k",
            pinned=False,
            route_tags=["runtime_truth"],
        ),
    ]
    winner, status, losers, _ = choose_effective_memory(group, cfg)
    assert winner == "m2"
    assert status == ConflictStatus.RESOLVED_BY_RUNTIME_TRUTH.value
    assert "m1" in losers


def test_stored_resolution_preserved_when_candidates_unchanged() -> None:
    cfg = RoutingConfig.default()
    envs = [
        MemoryEnvelope(memory_id="a", room="r", summary="x", conflict_key="k"),
        MemoryEnvelope(memory_id="b", room="r", summary="y", conflict_key="k"),
    ]
    stored = [
        ConflictRecord(
            conflict_key="k",
            room="r",
            candidate_memory_ids=["a", "b"],
            resolved_memory_id="a",
            loser_memory_ids=["b"],
            status=ConflictStatus.RESOLVED_BY_PIN.value,
            resolution_reason="explicit",
        )
    ]
    out = detect_conflicts(envs, cfg, stored=stored)
    assert len(out) == 1
    assert out[0].resolved_memory_id == "a"
