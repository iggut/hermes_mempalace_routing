## 2025-05-15 - Loop-invariant precomputation in scoring
**Learning:** In the routing layer, `RouteScorer.score` was being called in tight loops over all stored memory envelopes. Operations like splitting the query string, fetching the current time, and calculating the recency decay constant were being repeated for every envelope, leading to significant CPU waste.
**Action:** Precompute query tokens and recency constants once per retrieval request and pass them as optional arguments to the scoring function to avoid redundant work in hot loops.
