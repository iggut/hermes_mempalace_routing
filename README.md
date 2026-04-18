# hermes-mempalace-routing

A starter repository for a Hermes integration that replaces generic summarization with MemPalace route-aware context assembly.

## What this starter includes

- raw artifact persistence
- JSONL memory envelopes
- simple route scoring
- context budget allocation
- starter CLI:
  - `hermes-mp route`
  - `hermes-mp inspect`
  - `hermes-mp pin`
  - `hermes-mp conflicts`
- explicit Hermes hook placeholders in `plugin.py`
- basic tests for routing and budget allocation

## Status

This is an MVP skeleton, not a full Hermes plugin drop-in.
It is designed to be easy to graft into an existing Hermes codebase.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
```

## CLI examples

```bash
hermes-mp route "why is hermes failing to start" --active-project project/hermes
hermes-mp inspect room project/hermes
hermes-mp pin mem_art_20260418T010101000000Z --reason "runtime truth"
hermes-mp conflicts --room project/hermes
```

## Recommended Hermes integration points

### Pre-model context assembly
Replace the current summarization/compression stage with a call into:

- `HermesMemPalaceRoutingPlugin.build_context_for_query(...)`

### Post-turn artifact ingestion
After each user turn, tool output, shell command, or assistant reply, call:

- `HermesMemPalaceRoutingPlugin.record_turn_artifact(...)`

### Operator diagnostics / observability
Wire CLI or debug UI to:

- `route`
- `inspect`
- `pin`
- `conflicts`

## Storage layout

By default:

```text
~/.hermes/mempalace-routing/
  raw/
  index/
  cache/
```

## Important rule

Keep raw logs, stack traces, stderr, and command output exact.
Do not compress or rewrite them before storage.
Only compress on the final outbound prompt path if you add a compressor later.
