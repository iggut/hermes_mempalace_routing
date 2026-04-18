from hermes_mempalace_routing.conflicts import ConflictResolver
from hermes_mempalace_routing.models import MemoryEnvelope


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
    assert conflicts[0].resolution_reason == "pinned"
