import time
import math
from datetime import UTC, datetime, timedelta
from hermes_mempalace_routing.models import MemoryEnvelope, ConflictRecord, ConflictStatus
from hermes_mempalace_routing.routing import RouteScorer
from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.context_engine import RoutingContextEngine

def benchmark():
    config = RoutingConfig.default()
    scorer = RouteScorer(config)
    engine = RoutingContextEngine(scorer, config)

    # Create 1000 envelopes
    envelopes = []
    now = datetime.now(UTC)
    for i in range(1000):
        envelopes.append(MemoryEnvelope(
            memory_id=f"mem_{i}",
            room=f"room_{i % 10}",
            summary=f"Summary of memory {i} with some keywords like apple banana cherry",
            route_tags=[f"tag_{j}" for j in range(3)],
            created_at=(now - timedelta(days=i%10)).isoformat(),
            conflict_key=f"conflict_{i // 20}" if i < 400 else None,
            provenance_artifact_ids=["art_1"] # Needs provenance to not return early
        ))

    # Create 20 conflicts
    conflicts = []
    for i in range(20):
        conflicts.append(ConflictRecord(
            conflict_key=f"conflict_{i}",
            room=f"room_{i % 10}",
            candidate_memory_ids=[f"mem_{j}" for j in range(i*20, (i+1)*20)],
            status=ConflictStatus.UNRESOLVED.value if i % 2 == 0 else "resolved_by_pin",
            loser_memory_ids=[f"mem_{j}" for j in range(i*20 + 1, (i+1)*20)] if i % 2 != 0 else []
        ))

    query = "apple banana"
    mode = "debugging"
    active_project = None

    # Warm up
    engine.select_route_candidates(query, envelopes, active_project, mode, conflicts)

    n_runs = 100
    start = time.perf_counter()
    for _ in range(n_runs):
        engine.select_route_candidates(query, envelopes, active_project, mode, conflicts)
    end = time.perf_counter()

    print(f"Time for {n_runs} calls: {end - start:.4f} seconds")
    print(f"Average time per call: {(end - start) / n_runs:.6f} seconds")

if __name__ == "__main__":
    benchmark()
