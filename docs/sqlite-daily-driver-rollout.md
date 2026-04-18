# SQLite daily-driver rollout (single-user)

**Target:** First real daily-driver use on the **SQLite production path** only. **JSONL** remains legacy/best-effort—not part of go-live. Assumes Sprint 4 / 4.1 complete (MemPalace-boundary validation, strict scope, repeated-error suppression, `validate` CLI alias hardening).

**Out of scope unless explicitly accepted:** Live MCP/network interoperability proof, JSONL parity, environments with **only** estimated tokenizer validation (document that caveat if you roll out anyway).

---

## Minimum command set

```bash
pip install -e .
pytest

hermes-mp --base-dir ~/.hermes/mempalace-routing doctor
hermes-mp --base-dir ~/.hermes/mempalace-routing migrate
hermes-mp --base-dir ~/.hermes/mempalace-routing stats

hermes-mp --base-dir /tmp/mp-eval eval run --fixtures fixtures/eval --no-matrix
hermes-mp --base-dir /tmp/mp-eval eval run --fixtures fixtures/eval --json --output eval-report.json --strict
hermes-mp --base-dir /tmp/mp-eval eval tokenizer-fit --fixtures fixtures/eval --json
```

---

## Go / no-go gates

| Gate | Pass | No-go |
|------|------|--------|
| **1 — Environment** | Clean install; **pytest** green; SQLite **explicit or default**; eval fixtures runnable; **tiktoken** installed for measured tokenizer cells *or* you **explicitly** accept estimate-only | Tests fail; SQLite not active; tokenizer expectations unclear |
| **2 — Store health** | `doctor` / `migrate` / `stats` clean: no schema mismatch, no missing raw artifacts, no corruption/missing tables, migration current, stats plausible | Doctor failures; inconsistent migrations |
| **3 — Validation** | `eval run` (matrix off + **strict** JSON) exits 0; no unresolved functional failures; no MemPalace compatibility gaps on required fixtures; strict wing/room scope + repeated-error suppression pass; rollout summary has no blocking gaps | Functional / MemPalace-boundary / strict-scope failures |
| **4 — Tokenizer** | `eval tokenizer-fit --json`: no over-budget rendered blocks; measured cases pass where support exists; estimate-only OK **only** with explicit approval | Budget overruns; env differs materially from validation without acceptance |
| **5 — MemPalace boundary** | Fixtures cover wing-filtered, room-filtered, verbatim drawer-style retrieval; duplicate-aware filing + taxonomy/status expectations; reports separate **internal routing** vs **MemPalace-compatible** success—both pass where required | Only internal routing passes; MemPalace-compatible checks fail |
| **6 — Daily-driver smoke** | Pin/unpin updates retrieval immediately; conflict winner is correct; **redaction on by default**; `inspect` shows masking where expected; repeated stacktraces/stderr do not bloat the store | Truth/redaction inconsistent or store bloat |

---

## Release procedure (stages)

**Stage 1 — Snapshot**

```bash
cp -a ~/.hermes/mempalace-routing ~/.hermes/mempalace-routing.pre-rollout-backup
hermes-mp --base-dir /tmp/mp-eval eval run --fixtures fixtures/eval --json --output pre-rollout-report.json --strict
```

Keep backup + JSON report.

**Stage 2 — Shadow (1–2 days)**  
Parallel to old workflow: real debug/design queries, one conflict-resolution flow, one repeated-error scenario. Monitor `doctor`, `stats`, `route … --json`, rerun validation end of day.

**Stage 3 — Limited daily-driver**  
Switch normal workflow to SQLite. **First week:** no JSONL mode; keep backups; `doctor` daily; re-run full eval after any config/migration change; archive strict JSON reports.

**Stage 4 — Stabilization**  
After several clean days: compare eval to pre-rollout baseline; confirm no new MemPalace gaps, store drift, or unexplained retrieval regressions.

---

## Rollback triggers (act immediately)

- `doctor`: schema mismatch, missing raws, corruption symptoms  
- Strict eval fails after a change  
- Repeated diagnostics inflate envelopes beyond expectations  
- Conflict winner inconsistent  
- Redaction stops masking obvious secrets  
- Rendered prompt blocks over budget in real use  
- MemPalace-boundary cases fail while internal routing still looks fine  

**Soft rollback:** Disable routed path / feature flag; fall back to Hermes summarization; keep store for forensics; run `doctor` + strict eval → `rollback-diagnosis.json`.

**Hard rollback:** Stop using rollout store; `rm -rf ~/.hermes/mempalace-routing` then `cp -a` from `~/.hermes/mempalace-routing.pre-rollout-backup`; disable routed mode; `doctor` + `migrate`; re-enable only after a clean validation run.

---

## Final go decision

**Go** only if: SQLite active, tests pass, doctor clean, strict eval passes, no blocking MemPalace gaps, no prompt-budget failures, redaction + truth-management smoke tests pass.

**No-go** if: MemPalace-compatible checks fail while internal routing passes; you need measured tokenizer coverage but only have estimate-only and are not comfortable; degraded-path or store health issues; rollback path has not been exercised once.
