You are modifying Hermes to add a new integration package named `hermes_mempalace_routing`.

Goal:
Replace generic summarization/compression for working context with a MemPalace-aware routing layer that:
- stores raw artifacts exactly
- stores memory envelopes with provenance
- allocates token budget by room/route
- injects only top route evidence into the model prompt
- exposes CLI commands for route inspection, pins, and conflict review

Non-negotiable rules:
1. Do not compress or rewrite raw logs/errors/stack traces before storage.
2. Raw artifacts are the source of truth. Memory envelopes are indexing/routing handles.
3. Provenance must be preserved on every injected item.
4. Retrieval should inject top route evidence only, not a blended memory dump.
5. If any prompt compression exists later, it must happen only on the final outbound prompt path, after storage and route selection.

Implement the following package layout:

- `hermes_mempalace_routing/__init__.py`
- `hermes_mempalace_routing/config.py`
- `hermes_mempalace_routing/models.py`
- `hermes_mempalace_routing/storage.py`
- `hermes_mempalace_routing/provider.py`
- `hermes_mempalace_routing/routing.py`
- `hermes_mempalace_routing/context_engine.py`
- `hermes_mempalace_routing/conflicts.py`
- `hermes_mempalace_routing/plugin.py`
- `hermes_mempalace_routing/cli.py`

Data model requirements:
- `RawArtifact`: exact persisted artifact with file path, SHA256, kind, timestamps, turn id.
- `MemoryEnvelope`: room, fact_type, summary, route_tags, provenance_artifact_ids, confidence, pinned, conflict_key.
- `RouteCandidate`: room, memory_id, score, rationale.
- `ContextBudget`: total_tokens, live_conversation, routed_memory, raw_diagnostics, reserve.
- `InjectedEvidence`: memory_id, room, summary, provenance, optional raw_excerpt.
- `ConflictRecord`: conflict_key, room, candidate_memory_ids, resolved_memory_id, resolution_reason.

Room model:
- `identity`
- `ops`
- `errors`
- `decisions`
- `scratch`
- `pinned`
- `project/<name>`

Routing behavior:
- Bias route selection toward active project room.
- In debugging mode, boost `stacktrace`, `shell_output`, and `tool_output`.
- Boost pinned memories.
- Prefer exact term overlap in summary and route tags.
- Expose route rationale for debugging.

Context engine requirements:
- Replace Hermes summarization via a wrapper hook.
- Allocate default budget fractions:
  - 20% live conversation
  - 35% routed memory
  - 15% raw diagnostics
  - 10% reserve
- Select top-K route candidates and top-K raw diagnostic excerpts.
- Render an injected evidence block with provenance references.

Storage requirements:
- Persist raw artifacts to disk under a dated directory tree.
- Append envelopes, pins, and conflicts to JSONL files.
- Keep implementation simple and deterministic.

CLI requirements:
- `hermes-mp route <query> [--active-project ...] [--mode debugging|design]`
- `hermes-mp inspect room <room>`
- `hermes-mp inspect memory <memory_id>`
- `hermes-mp inspect artifact <artifact_id>`
- `hermes-mp pin <memory_id> --reason ...`
- `hermes-mp conflicts [--room ...]`

Hermes hook points to add:
1. Pre-model context assembly hook:
   - call routing plugin before any summarization fallback
2. Post-turn artifact ingestion hook:
   - store user messages, assistant replies, tool output, shell output, stack traces
3. Optional diagnostics endpoint or CLI entry:
   - surface route selection and conflict state

Implementation guidance:
- Keep the starter repo installable with `pip install -e .`
- Add unit tests for budget allocation and route scoring
- Keep APIs typed and small
- Add clear TODO markers where Hermes-specific integration is required

Deliverables:
- working package skeleton
- CLI entrypoint
- basic tests passing
- README with quick-start and integration notes
