# hermes-mempalace-routing

MemPalace-aware routing layer for Hermes: store raw artifacts exactly, index with memory envelopes (provenance, rooms, tags), allocate context budget by channel, and inject **top-route evidence** (plus optional raw diagnostic excerpts) into the model prompt—not a blended memory dump.

## Non-negotiables

1. Do not compress or rewrite raw logs, errors, or stack traces **before** they are written to disk (unless **redaction policy** is enabled; see below).
2. Raw artifacts on disk are the source of truth; envelopes are routing/indexing handles.
3. Every injected item carries provenance (artifact ids).
4. Retrieval favors **ranked route candidates**; additional raw diagnostic excerpts are drawn from the same scored pool and deduped against inline evidence.
5. Any prompt compression, if added later, belongs only on the **final outbound** prompt path, after storage and route selection.

## Storage backends

- **SQLite (default, production):** Metadata in `metadata.db` under the storage root; raw files under `raw/`. This is the supported path for Sprint 3 operator tooling (`doctor`, `migrate`, `reindex`, `stats`).
- **JSONL (legacy):** Append-only `index/*.jsonl` plus raw files. Retained for compatibility. **Doctor** reports corrupt lines and missing raws; **reindex** is best-effort only. Prefer SQLite for new deployments.

## Redaction (safe defaults)

- Default config uses **`redact_before_persist=True`** and **`redaction_policy="mask"`** so obvious API keys, bearer tokens, and common `KEY=value` secret lines are masked **before** the raw file is written.
- Masking is reflected in **`RawArtifact.redaction_status`** (`none` / `masked` / `dropped`) and in **`hermes-mp stats`** redaction counts.
- **`hermes-mp inspect artifact`** shows the **stored** bytes (redacted when applicable), not the pre-redaction secret.
- Limitations: pattern-based only; not a full secret scanner. Use `redaction_policy="none"` only when you fully trust the environment.

## Truth management and conflicts

- Conflicts are keyed by **`conflict_key`** on envelopes. The engine computes an **effective winner** using configurable **precedence** (`RoutingConfig.conflict_precedence`, default: `runtime_truth` → `pin` → `newer_verified`).
- Mark runtime-selected truth by adding the route tag **`runtime_truth`** on the winning envelope (or via your integration).
- **Resolved** conflicts persist losers in metadata; **losers are excluded from retrieval** (they do not silently “win” via scoring).
- **Unresolved** conflicts apply a scoring penalty; they never become implicit truth.
- Use **`hermes-mp resolve-conflict`** to pin an explicit winner; use **`hermes-mp pin` / `hermes-mp unpin`** to change pin state (SQLite updates envelopes immediately for retrieval).

## Fail-open behavior

- When **`fail_open_to_hermes_summarization`** is enabled (default), storage/ingest failures during `record_turn_artifact` return **`None`** instead of breaking chat.
- Routing failures in **`build_context_for_query`** return a fallback payload with **`fallback_used=True`** and a traced **`RouteRun`** when possible.

## Package layout

| Module | Role |
|--------|------|
| `config.py` | `RoutingConfig`, precedence, redaction, dedupe knobs |
| `models.py` | Artifacts, envelopes, conflicts, doctor/stats DTOs |
| `storage.py` | `create_storage()`, protocol, JSONL legacy |
| `storage_sqlite.py` | SQLite persistence, doctor, reindex, stats |
| `migrations.py` | Schema versions |
| `redaction.py` | Mask/drop policies before persistence |
| `routing.py` | `RouteScorer` + conflict loser exclusion |
| `context_engine.py` | Budget, selection, render, fit |
| `provider.py` | Deterministic ingest pipeline (validate → redact → dedupe → classify → persist → sync conflicts) |
| `conflicts.py` | Detect/resolve/list, effective memory selection |
| `plugin.py` | Thin Hermes facade (orchestration + fail-open boundary) |
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

## CLI (Sprint 3)

Global flags (place **before** the subcommand): `--base-dir PATH`, optional `--storage {sqlite,jsonl}`.

```bash
# Health and schema
hermes-mp --base-dir ~/.hermes/mempalace-routing doctor
hermes-mp --base-dir ~/.hermes/mempalace-routing migrate
hermes-mp --base-dir ~/.hermes/mempalace-routing stats

# Rebuild missing DB rows from raw files (SQLite; default dry-run)
hermes-mp --base-dir ~/.hermes/mempalace-routing reindex
hermes-mp --base-dir ~/.hermes/mempalace-routing reindex --apply

# Routing (human-readable)
hermes-mp --base-dir ~/.hermes/mempalace-routing route "why is hermes failing" --active-project hermes --mode debugging

# Machine-readable route payload (stable JSON, sorted keys)
hermes-mp --base-dir ~/.hermes/mempalace-routing route "query" --json --mode debugging

# Inspection
hermes-mp --base-dir ~/.hermes/mempalace-routing inspect room project/hermes
hermes-mp --base-dir ~/.hermes/mempalace-routing inspect memory mem_art_...
hermes-mp --base-dir ~/.hermes/mempalace-routing inspect artifact art_...

# Pins and conflicts
hermes-mp --base-dir ~/.hermes/mempalace-routing pin mem_... --reason "operator pin"
hermes-mp --base-dir ~/.hermes/mempalace-routing unpin mem_... --reason "operator unpin"
hermes-mp --base-dir ~/.hermes/mempalace-routing conflicts --room project/hermes
hermes-mp --base-dir ~/.hermes/mempalace-routing resolve-conflict my.key --winner mem_... --actor operator --reason explicit
```

- **`doctor`:** Nonzero exit if blocking issues (migration mismatch, missing tables, missing raw files referenced by SQLite).  
- **`migrate`:** Applies pending SQLite migrations; prints expected vs applied versions.  
- **`reindex`:** SQLite: scans `raw/**/*.txt` and inserts missing artifact/envelope rows; use `--apply` to write. JSONL: informational only.

## Hermes integration (hook points)

1. **Pre-model context assembly**  
   Call `HermesMemPalaceRoutingPlugin.build_context_for_query(...)` **before** any generic summarization/compression fallback. Merge `payload["rendered_block"]` (or structured `evidence` + `raw_diagnostic_excerpts`) into the outbound prompt assembly.

2. **Post-turn artifact ingestion**  
   After user messages, assistant replies, tool output, shell output, or stack traces, call `record_turn_artifact(...)` with the correct `room`, `fact_type`, and **exact** `raw_text`. Envelope `summary` may be hand-written or model-generated for routing; it must not replace stored raw content (unless redaction policy applies before write).

3. **Diagnostics**  
   Expose `hermes-mp` commands from an operator CLI; optionally surface `payload["route_candidates"]` and `route --json` in internal telemetry.

Storage root defaults to `~/.hermes/mempalace-routing/` (override with `RoutingConfig.base_dir` or CLI `--base-dir`).

## Layout on disk (SQLite default)

```text
~/.hermes/mempalace-routing/
  metadata.db
  raw/YYYY/MM/DD/art_*_<kind>.txt
  index/          # reserved / cache
  cache/
```

JSONL legacy additionally uses `index/*.jsonl` as an append-only index (see `storage.py`).

## Example: `route --json` shape

The JSON payload includes query, mode, active project, budget fractions, ranked `route_candidates`, selected and dropped evidence ids with reasons, `fallback_used`, and token count estimates. Use it for automation and CI checks; field names are stable within a release series.
