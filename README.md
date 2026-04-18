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

## CLI (operator)

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

### Sprint 4: validation and rollout evidence

Sprint 4 adds **fixture-driven validation** (not a redesign of storage or routing). It answers: prompt/token **fit**, **retrieval** quality on representative Hermes/MemPalace workflows, and **degraded-path** behavior (fail-open, doctor, redaction toggles, pins). Reports are **stable JSON** (sorted keys) suitable for CI artifacts and archival evidence.

**What Sprint 4 validates**

- Tokenizer / provider fit: synthetic stress blocks through `RoutingContextEngine.fit_to_token_budget`, optional **provider/model** matrix (`tokenizer_strategy=auto` uses tiktoken when installed, otherwise **estimated** tokens — the report labels **measured** vs **estimated** explicitly).
- Retrieval: recall@k, wrong-room rate, conflict-loser leakage, optional raw-diagnostic expectations, legacy baseline comparison (non-routed “first-k summaries”).
- Operations: isolated per-case stores under `base_dir/_eval_ops/<case_id>/` — doctor on a fresh DB, forced routing failure fail-open, missing raw file, `insert_route_run` failure fail-open (second insert succeeds), SQLite unpin, redaction policy reporting. **JSONL** remains legacy: some checks report `validation_gap` where SQLite-only features apply.

**What Sprint 4 does *not* validate**

- It does not prove behavior on every external LLM provider API; tokenizer paths are **tiktoken or estimate** only in this package.
- It does not replace integration tests inside Hermes itself; it validates this library’s routing + storage contracts.

**Commands**

```bash
# From a checkout (fixtures live under ./fixtures/eval)
hermes-mp --base-dir /tmp/mp-eval eval run --fixtures fixtures/eval
hermes-mp --base-dir /tmp/mp-eval eval tokenizer-fit --fixtures fixtures/eval --json
hermes-mp --base-dir /tmp/mp-eval eval retrieval --fixtures fixtures/eval/01_retrieval.json
hermes-mp --base-dir /tmp/mp-eval eval ops --fixtures fixtures/eval/03_ops.json

# Automation: JSON to stdout or file; nonzero exit on failures (--strict also enforces global thresholds)
hermes-mp --base-dir /tmp/mp-eval eval run --fixtures fixtures/eval --json --output eval-report.json --strict
hermes-mp --base-dir /tmp/mp-eval eval retrieval --min-recall-at-k 0.8 --max-wrong-room-rate 0.25 --strict
```

**Interpreting pass/fail and go/no-go**

- **Case pass:** each fixture case must pass its own expectations (recall, exclusions, fit under injection cap after the safety multiplier, operational checks).
- **Tokenizer matrix:** all matrix cells must pass fit (no budget overage after conservative counting). If **`measurement_kind` is `estimated` everywhere**, treat tokenizer proof as **conservative / heuristic** until tiktoken (or a future tokenizer binding) is available in the environment — the report states this explicitly.
- **Go / no-go for SQLite production (limited rollout):** require repeated clean runs of `eval run` on a dedicated `--base-dir`, `doctor` clean on the real store, and `migrate` at the expected schema version. **Do not** treat JSONL-only doctor/reindex behavior as production proof.

**Recommended rollout stages**

1. **Local developer:** run `eval run` from the repo after changes; archive JSON reports occasionally.
2. **Single-user daily driver:** SQLite store under `~/.hermes/mempalace-routing`, run `eval run` before upgrades; monitor `hermes-mp doctor`, `stats`, and `route --json` for anomalies.
3. **Wider rollout:** only after multiple clean validation runs on representative machines (Python version, optional `tiktoken` installed for measured tokenizer cells).

**Post-rollout monitoring (existing CLI)**

- `hermes-mp doctor`, `stats`, `route --json` for payload shape drift; `conflicts` / `resolve-conflict` when editing truth; `reindex --apply` only after understanding doctor output.

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
