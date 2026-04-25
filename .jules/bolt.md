## 2025-05-15 - Loop-invariant precomputation in scoring
**Learning:** In the routing layer, `RouteScorer.score` was being called in tight loops over all stored memory envelopes. Operations like splitting the query string, fetching the current time, and calculating the recency decay constant were being repeated for every envelope, leading to significant CPU waste.
**Action:** Precompute query tokens and recency constants once per retrieval request and pass them as optional arguments to the scoring function to avoid redundant work in hot loops.

## 2026-04-25 - Efficient conflict lookups in scoring path
**Learning:** Conflict lookups during scoring were a major bottleneck (O(N_envelopes * N_conflicts)) because each envelope was being checked against a list of conflict records. In environments with many envelopes and conflicts, this resulted in poor retrieval performance.
**Action:** Convert conflict records into precomputed hash sets of memory IDs (losers and unresolved) before entering the scoring loop. This changes the lookup complexity from O(N*M) to O(N+M) and resulted in a measured performance speedup of approximately 293% in scenarios with 500 conflicts.
