# hermes-mempalace-routing

MemPalace-aware routing layer for Hermes: store raw artifacts exactly, index with memory envelopes (provenance, rooms, tags), allocate context budget by channel, and inject **top-route evidence** (plus optional raw diagnostic excerpts) into the model prompt—not a blended memory dump.

## Non-negotiables

1. Do not compress or rewrite raw logs, errors, or stack traces **before** they are written to disk.
2. Raw artifacts on disk are the source of truth; envelopes are routing/indexing handles.
3. Every injected item carries provenance (artifact ids).
4. Retrieval favors **ranked route candidates**; additional raw diagnostic excerpts are drawn from the same scored pool and deduped against inline evidence.
5. Any prompt compression, if added later, belongs only on the **final outbound** prompt path, after storage and route selection.

## Package layout

| Module | Role |
|--------|------|
| `config.py` | `RoutingConfig`, standard rooms, `project_room()` |
| `models.py` | `RawArtifact`, `MemoryEnvelope`, `RouteCandidate`, `ContextBudget`, `InjectedEvidence`, `RawDiagnosticExcerpt`, `ConflictRecord` |
| `storage.py` | Dated raw files under `raw/`, JSONL index (`envelopes`, `artifacts`, `pins`, `conflicts`) |
| `routing.py` | `RouteScorer`: project bias, debugging diagnostic boost, pinned boost, term overlap |
| `context_engine.py` | Budget fractions, top-K evidence, top-K raw diagnostic excerpts, rendered block |
| `provider.py` | Persist raw + envelope in one call |
| `plugin.py` | Hermes-facing facade + `TODO(Hermes)` hook markers |
| `conflicts.py` | Conflict detection by `conflict_key` |
| `cli.py` | `hermes-mp` operator commands |

## Room model

Standard loci: `identity`, `ops`, `errors`, `decisions`, `scratch`, `pinned`, plus `project/<name>` (see `project_room()` in `config.py`).

## Default context budget (tokens)

| Slice | Share |
|-------|--------|
| Live conversation | 20% |
| Routed memory | 35% |
| Raw diagnostics | 15% |
| Reserve | 10% |
| Remainder | Whatever is left (reported explicitly) |

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
```

## CLI

```bash
hermes-mp route "why is hermes failing to start" --active-project hermes --mode debugging
hermes-mp inspect room project/hermes
hermes-mp inspect memory mem_art_20260418T010101000000Z
hermes-mp inspect artifact art_20260418T010101000000Z
hermes-mp pin mem_art_20260418T010101000000Z --reason "runtime truth"
hermes-mp conflicts --room project/hermes
```

`hermes-mp route` prints ranked candidates with **score and rationale** for debugging, then the rendered evidence blocks.

## Hermes integration (hook points)

1. **Pre-model context assembly**  
   Call `HermesMemPalaceRoutingPlugin.build_context_for_query(...)` **before** any generic summarization/compression fallback. Merge `payload["rendered_block"]` (or structured `evidence` + `raw_diagnostic_excerpts`) into the outbound prompt assembly.

2. **Post-turn artifact ingestion**  
   After user messages, assistant replies, tool output, shell output, or stack traces, call `record_turn_artifact(...)` with the correct `room`, `fact_type`, and **exact** `raw_text`. Envelope `summary` may be hand-written or model-generated for routing; it must not replace stored raw content.

3. **Diagnostics**  
   Expose `hermes-mp route` / `inspect` / `conflicts` from an operator CLI or debug endpoint; optionally surface `payload["route_candidates"]` in internal telemetry.

Storage root defaults to `~/.hermes/mempalace-routing/` (override with `RoutingConfig.base_dir` or CLI `--base-dir`).

## Layout on disk

```text
~/.hermes/mempalace-routing/
  raw/YYYY/MM/DD/art_*_<kind>.txt
  index/artifacts.jsonl
  index/envelopes.jsonl
  index/pins.jsonl
  index/conflicts.jsonl
  cache/
```
