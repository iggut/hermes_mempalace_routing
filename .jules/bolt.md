## 2025-05-15 - Loop-invariant precomputation in scoring
**Learning:** In the routing layer, `RouteScorer.score` was being called in tight loops over all stored memory envelopes. Operations like splitting the query string, fetching the current time, and calculating the recency decay constant were being repeated for every envelope, leading to significant CPU waste.
**Action:** Precompute query tokens and recency constants once per retrieval request and pass them as optional arguments to the scoring function to avoid redundant work in hot loops.

## 2025-05-16 - O(N*M) conflict lookups in scoring
**Learning:** Conflict lookups during scoring were a major bottleneck (O(N_envelopes * N_conflicts)). Each envelope was checking against every conflict record to see if it was a loser or part of an unresolved conflict.
**Action:** Convert conflict records into hash sets (O(1) lookups) for losers and unresolved candidates once per retrieval request. Pass these sets to the scoring function to eliminate the linear scan inside the scoring loop.
