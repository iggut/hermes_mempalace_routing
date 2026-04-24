## 2025-05-15 - Loop-invariant precomputation in scoring
**Learning:** In the routing layer, `RouteScorer.score` was being called in tight loops over all stored memory envelopes. Operations like splitting the query string, fetching the current time, and calculating the recency decay constant were being repeated for every envelope, leading to significant CPU waste.
**Action:** Precompute query tokens and recency constants once per retrieval request and pass them as optional arguments to the scoring function to avoid redundant work in hot loops.

## 2026-04-24 - Hash set lookups for conflict resolution
**Learning:** Conflict lookups during scoring were a major bottleneck ((N\_envelopes * N\_conflicts)$). Converting them to hash set lookups ((1)$) resulted in an ~293% measured performance speedup for cases with many conflicts.
**Action:** Always prefer precomputing and passing hash sets for ID-based lookups when iterating over large collections in a hot loop.
