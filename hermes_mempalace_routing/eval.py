"""
Sprint 4 validation harness: tokenizer fit, retrieval quality, operational safety.

Reports are JSON-serializable dicts with stable key ordering at dump time (sort_keys=True).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from .config import RoutingConfig
from .conflicts import resolve_conflict
from .migrations import expected_migration_versions
from .context_engine import RoutingContextEngine
from .models import InjectedEvidence, MemoryEnvelope, RawDiagnosticExcerpt, RouteCandidate
from .plugin import HermesMemPalaceRoutingPlugin
from .provider import MemPalaceRoutingProvider
from .routing import RouteScorer, room_matches_active_project
from .storage import StorageBackend, UnsupportedStorageOperation, create_storage
from .tokenizer import count_tokens, estimate_tokens_fallback

CaseKind = Literal["retrieval", "tokenizer_fit", "operational"]
EvidenceSize = Literal["short", "medium", "long"]


def default_fixtures_dir() -> Path:
    """Directory containing `fixtures/eval` relative to the package source tree."""
    return Path(__file__).resolve().parent.parent / "fixtures" / "eval"


def _load_yaml_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suf = path.suffix.lower()
    if suf in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "YAML fixtures require PyYAML; install with `pip install pyyaml` or use .json fixtures."
            ) from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"Fixture root must be a mapping: {path}")
        return data
    return json.loads(text)


def load_fixture_file(path: Path | str) -> dict[str, Any]:
    """Load a single JSON or YAML fixture file."""
    p = Path(path)
    return _load_yaml_or_json(p)


def load_fixture_paths(paths: Sequence[Path | str]) -> dict[str, Any]:
    """Merge multiple fixture files into one suite (cases and seeds concatenated)."""
    merged: dict[str, Any] = {
        "suite_id": "fixtures_eval_merged",
        "version": 1,
        "seed": [],
        "cases": [],
        "seed_resolutions": [],
    }
    first_suite_id: str | None = None
    for i, raw in enumerate(paths):
        p = Path(raw)
        data = _load_yaml_or_json(p)
        if i == 0 and data.get("suite_id"):
            first_suite_id = str(data.get("suite_id"))
        merged["version"] = max(merged["version"], int(data.get("version") or 1))
        merged["seed"].extend(list(data.get("seed") or []))
        merged["cases"].extend(list(data.get("cases") or []))
        merged["seed_resolutions"].extend(list(data.get("seed_resolutions") or []))
    if len(paths) == 1 and first_suite_id:
        merged["suite_id"] = first_suite_id
    return merged


def load_fixture_dir(dir_path: Path | str | None = None) -> dict[str, Any]:
    """Load and merge all `*.json`, `*.yaml`, `*.yml` in a directory (sorted by name)."""
    root = Path(dir_path) if dir_path is not None else default_fixtures_dir()
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    files = sorted(
        [p for p in root.iterdir() if p.suffix.lower() in (".json", ".yaml", ".yml") and p.is_file()],
        key=lambda x: x.name,
    )
    if not files:
        raise FileNotFoundError(f"No JSON/YAML fixtures under {root}")
    return load_fixture_paths(files)


def tokenizer_measurement_kind(strategy: str) -> Literal["measured", "estimated"]:
    """Whether token counts use a real tokenizer (tiktoken) or the heuristic estimate."""
    if strategy in ("tiktoken", "auto"):
        try:
            import tiktoken  # type: ignore[import-untyped]

            tiktoken.get_encoding("cl100k_base")
            return "measured"
        except Exception:
            return "estimated"
    return "estimated"


def _tok_with_safety(
    text: str,
    *,
    model_hint: str | None,
    provider_hint: str | None,
    strategy: str,
    safety_multiplier: float,
) -> int:
    n = count_tokens(text, model_hint, provider_hint, strategy=strategy)
    return int(n * safety_multiplier)


@dataclass(slots=True)
class EvalCase:
    """Single validation case loaded from fixtures."""

    id: str
    kind: CaseKind
    query: str = ""
    mode: str = "debugging"
    active_project: str | None = None
    total_tokens: int = 12000
    category: str = "general"
    tokenizer_strategy: str | None = None
    provider_hint: str | None = None
    model_hint: str | None = None
    evidence_size: EvidenceSize | None = None
    expect_raw_diagnostics: bool | None = None
    expect: dict[str, Any] = field(default_factory=dict)
    ops: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> EvalCase:
        kind = str(m.get("kind") or "retrieval")
        if kind not in ("retrieval", "tokenizer_fit", "operational"):
            raise ValueError(f"Unknown case kind: {kind}")
        return cls(
            id=str(m["id"]),
            kind=kind,  # type: ignore[arg-type]
            query=str(m.get("query") or ""),
            mode=str(m.get("mode") or "debugging"),
            active_project=m.get("active_project"),
            total_tokens=int(m.get("total_tokens") or 12000),
            category=str(m.get("category") or "general"),
            tokenizer_strategy=m.get("tokenizer_strategy"),
            provider_hint=m.get("provider_hint"),
            model_hint=m.get("model_hint"),
            evidence_size=m.get("evidence_size"),  # type: ignore[arg-type]
            expect_raw_diagnostics=m.get("expect_raw_diagnostics"),
            expect=dict(m.get("expect") or {}),
            ops=dict(m.get("ops") or {}),
            notes=m.get("notes"),
        )


@dataclass(slots=True)
class TokenizerFitResult:
    case_id: str
    provider_hint: str | None
    model_hint: str | None
    mode: str
    evidence_size: str
    tokenizer_strategy: str
    measurement_kind: Literal["measured", "estimated"]
    safety_multiplier: float
    estimated_tokens_raw: int
    measured_tokens_raw: int
    budget_injection_cap: int
    rendered_tokens_after_safety: int
    fit_pass: bool
    budget_overage: bool
    trim_events: int
    fallback_tokenizer: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrievalQualityResult:
    case_id: str
    category: str
    recall_at_k: float
    top1_correct: bool
    wrong_room_rate: float
    conflict_leakage: bool
    raw_diag_included: bool | None
    fit_pass: bool
    selected_count: int
    route_candidates_top_ids: list[str]
    baseline_recall_at_k: float | None
    baseline_comparison: str | None
    # internal_route_pass: Hermes routing layer (recall/exclusions/fit/diagnostics).
    internal_route_pass: bool = True
    # mempalace_compatible_pass: wing/room/drawer/verbatim expectations when expect.mempalace is set.
    mempalace_compatible_pass: bool | None = None
    mempalace_checks: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OperationalCheckResult:
    case_id: str
    check: str
    ok: bool
    fail_open_reported: bool | None
    doctor_ok: bool | None
    legacy_jsonl: bool
    details: dict[str, Any] = field(default_factory=dict)
    validation_gap: bool = False
    notes: list[str] = field(default_factory=list)
    mempalace_signal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvalResult:
    case_id: str
    kind: CaseKind
    passed: bool
    tokenizer: TokenizerFitResult | None = None
    retrieval: RetrievalQualityResult | None = None
    operational: OperationalCheckResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "case_id": self.case_id,
            "kind": self.kind,
            "passed": self.passed,
            "error": self.error,
        }
        if self.tokenizer:
            out["tokenizer"] = self.tokenizer.to_dict()
        if self.retrieval:
            out["retrieval"] = self.retrieval.to_dict()
        if self.operational:
            out["operational"] = self.operational.to_dict()
        return out


@dataclass(slots=True)
class EvalSuiteReport:
    suite_id: str
    version: int
    storage_backend: str
    legacy_jsonl: bool
    results: list[EvalResult]
    summary: dict[str, Any]
    tokenizer_matrix: list[TokenizerFitResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "version": self.version,
            "storage_backend": self.storage_backend,
            "legacy_jsonl": self.legacy_jsonl,
            "results": [r.to_dict() for r in self.results],
            "summary": dict(sorted(self.summary.items())),
            "tokenizer_matrix": [t.to_dict() for t in self.tokenizer_matrix],
        }


def _cfg_for_eval(
    base_dir: Path,
    *,
    storage_backend: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> RoutingConfig:
    kwargs: dict[str, Any] = {"base_dir": base_dir}
    if storage_backend:
        kwargs["storage_backend"] = storage_backend  # type: ignore[assignment]
    cfg = RoutingConfig(**kwargs)  # type: ignore[misc]
    if overrides:
        for k, v in overrides.items():
            object.__setattr__(cfg, k, v)
    cfg.validate()
    return cfg


def _seed_storage(
    store: StorageBackend,
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Insert seed envelopes; return alias -> memory_id map."""
    aliases: dict[str, str] = {}
    for ent in entries:
        env = store.persist_memory_turn(
            turn_id=str(ent.get("turn_id") or "eval_seed"),
            room=str(ent["room"]),
            fact_type=str(ent.get("fact_type") or "note"),
            summary=str(ent.get("summary") or ""),
            raw_text=str(ent.get("raw_text") or ""),
            route_tags=list(ent.get("route_tags") or []),
            conflict_key=ent.get("conflict_key"),
            pinned=bool(ent.get("pinned", False)),
            classification_source=str(ent.get("classification_source") or "rule"),
            verification_status=str(ent.get("verification_status") or "unverified"),
            raw_redaction_status=str(ent.get("raw_redaction_status") or "none"),
        )
        alias = ent.get("alias")
        if alias:
            aliases[str(alias)] = env.memory_id
    return aliases


def _apply_seed_resolutions(
    store: StorageBackend,
    aliases: Mapping[str, str],
    entries: Iterable[Mapping[str, Any]],
) -> None:
    envs = store.list_envelopes()
    for ent in entries:
        winner = _resolve_id_list([ent["winner"]], aliases)[0]
        rec = resolve_conflict(
            conflict_key=str(ent["conflict_key"]),
            winner_memory_id=winner,
            actor=str(ent.get("actor") or "eval"),
            reason=str(ent.get("reason") or "fixture_resolution"),
            envelopes=envs,
        )
        store.append_conflict(rec)


def _resolve_id_list(vals: Any, aliases: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for v in vals or []:
        s = str(v)
        if s.startswith("alias:"):
            key = s.split(":", 1)[1]
            if key not in aliases:
                raise KeyError(f"Unknown alias {key!r}")
            out.append(aliases[key])
        else:
            out.append(s)
    return out


def _mempalace_retrieval_checks(
    mp: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    payload: Mapping[str, Any],
    aliases: Mapping[str, str],
    rendered: str,
    selected_ids: Sequence[str],
    selected_rooms: Sequence[str],
    envelope_rooms: Mapping[str, str],
) -> tuple[dict[str, Any], bool]:
    """Validate MemPalace-facing semantics: wing, room, drawer provenance, verbatim drawer bytes in prompt."""
    checks: dict[str, Any] = {}
    ok = True

    wing = mp.get("expected_wing")
    if wing is not None:
        w = str(wing)
        strict_all = bool(mp.get("wing_scope_all_selected"))
        if strict_all:
            bad = [r for r in selected_rooms if not room_matches_active_project(r, w)]
            checks["wing_scope_all_selected_ok"] = len(bad) == 0
            checks["wing_violation_rooms"] = bad
            if bad:
                ok = False
        else:
            hit_ids = _resolve_id_list(mp.get("wing_check_memory_ids") or expect.get("memory_ids_in_top_k") or [], aliases)
            if mp.get("expected_drawer_memory_id"):
                hit_ids = _resolve_id_list([mp.get("expected_drawer_memory_id")], aliases)
            rooms_for_hits = [envelope_rooms[mid] for mid in hit_ids if mid in envelope_rooms]
            bad_hits = [r for r in rooms_for_hits if not room_matches_active_project(r, w)]
            checks["wing_expected_hits_under_wing"] = len(bad_hits) == 0
            checks["wing_violation_rooms_for_expected_hits"] = bad_hits
            if bad_hits:
                ok = False

    exp_room = mp.get("expected_room")
    if exp_room is not None:
        er = str(exp_room).lower().strip()
        strict_room = bool(mp.get("room_scope_all_selected"))
        if strict_room:
            room_ok = bool(selected_rooms) and all(
                str(r).lower().strip() == er or str(r).lower().strip().startswith(er + "/") for r in selected_rooms
            )
        else:
            hit_ids = _resolve_id_list(mp.get("room_check_memory_ids") or expect.get("memory_ids_in_top_k") or [], aliases)
            if mp.get("expected_drawer_memory_id"):
                hit_ids = _resolve_id_list([mp.get("expected_drawer_memory_id")], aliases)
            rooms_for_hits = [envelope_rooms[mid] for mid in hit_ids if mid in envelope_rooms]
            room_ok = bool(rooms_for_hits) and all(
                str(r).lower().strip() == er or str(r).lower().strip().startswith(er + "/") for r in rooms_for_hits
            )
        checks["room_filter_ok"] = room_ok
        if not room_ok:
            ok = False

    dm = mp.get("expected_drawer_memory_id")
    if dm is not None:
        mid = _resolve_id_list([dm], aliases)[0]
        checks["drawer_memory_in_selected"] = mid in selected_ids
        if mid not in selected_ids:
            ok = False

    prov_expect = mp.get("expected_drawer_provenance_artifact_ids")
    if prov_expect is not None:
        expected_aids = {_resolve_id_list([x], aliases)[0] for x in prov_expect}
        prov_union: list[str] = []
        for e in payload.get("evidence") or []:
            prov_union.extend(getattr(e, "provenance", []) or [])
        for chunk in payload.get("raw_diagnostic_excerpts") or []:
            prov_union.append(getattr(chunk, "artifact_id", "") or "")
        seen = set(prov_union)
        checks["drawer_provenance_ids_visible"] = sorted(expected_aids)
        checks["drawer_provenance_ok"] = expected_aids.issubset(seen)
        if not expected_aids.issubset(seen):
            ok = False

    verb = mp.get("verbatim_substring_in_rendered")
    if verb is not None:
        vs = str(verb)
        checks["verbatim_in_rendered"] = vs in rendered
        if vs not in rendered:
            ok = False

    return checks, ok


def _legacy_baseline_text(envelopes: Sequence[MemoryEnvelope], k: int) -> str:
    envs = sorted(envelopes, key=lambda e: e.memory_id)[: max(0, k)]
    lines = ["[Legacy baseline: non-routed first-k summaries]"]
    for e in envs:
        lines.append(f"- {e.memory_id} ({e.room}): {e.summary}")
    return "\n".join(lines)


def _expected_in_top_k(
    ranked: Sequence[RouteCandidate],
    expected_ids: Sequence[str],
    k: int,
) -> tuple[float, bool]:
    if not expected_ids:
        return 1.0, True
    top = [c.memory_id for c in ranked[:k]]
    hits = sum(1 for eid in expected_ids if eid in top)
    recall = hits / max(1, len(expected_ids))
    top1 = bool(top) and top[0] in expected_ids if expected_ids else True
    return recall, top1


def _wrong_room_rate(selected_rooms: Sequence[str], expected_rooms: Sequence[str]) -> float:
    if not selected_rooms:
        return 0.0
    exp = set(expected_rooms)
    if not exp:
        return 0.0
    wrong = sum(1 for r in selected_rooms if not any(r.startswith(er) or r == er for er in exp))
    return wrong / max(1, len(selected_rooms))


def _synthetic_evidence_items(
    *,
    prefix: str,
    evidence_size: EvidenceSize,
    stress_summary: bool = False,
) -> list[InjectedEvidence]:
    chars = {"short": 400, "medium": 3500, "long": 12000}[evidence_size]
    ev: list[InjectedEvidence] = []
    for i in range(4):
        body = ("line %d\n" % i) + ("x" * max(0, chars // 4))
        summ = f"synthetic summary {i} " + ("tokenstress " * 20) if stress_summary else "summary " + ("stress " * 40)
        ev.append(
            InjectedEvidence(
                memory_id=f"{prefix}_{i}",
                room="errors" if i % 2 == 0 else "scratch",
                summary=summ,
                provenance=[f"art_{i}"],
                raw_excerpt=body,
                source_score=1.0 - i * 0.01 if stress_summary else 1.0,
                confidence=0.5,
                pinned=False,
            )
        )
    return ev


def _fit_tokenizer_case(
    case_id: str,
    *,
    cfg: RoutingConfig,
    mode: str,
    evidence_size: EvidenceSize,
    total_tokens: int,
    id_prefix: str,
    stress_summary: bool = False,
) -> TokenizerFitResult:
    engine = RoutingContextEngine(RouteScorer(cfg), cfg)
    budget = engine.allocate_budget(total_tokens)
    max_inj = budget.routed_memory + budget.raw_diagnostics
    ev = _synthetic_evidence_items(prefix=id_prefix, evidence_size=evidence_size, stress_summary=stress_summary)
    raw_chunks: list[RawDiagnosticExcerpt] = []
    rendered = engine.render_injected_block(ev, raw_chunks)
    fitted, drops, _, _ = engine.fit_to_token_budget(rendered, ev, raw_chunks, max_inj)
    trim_events = len(drops)

    strat = cfg.tokenizer_strategy
    mkind = tokenizer_measurement_kind(strat)
    est = estimate_tokens_fallback(fitted)
    raw_measured = count_tokens(fitted, cfg.model_hint, cfg.provider_hint, strategy=strat)
    after_safety = _tok_with_safety(
        fitted,
        model_hint=cfg.model_hint,
        provider_hint=cfg.provider_hint,
        strategy=strat,
        safety_multiplier=cfg.tokenizer_fallback_safety_multiplier,
    )
    fit_pass = after_safety <= max_inj
    overage = after_safety > max_inj
    fallback = mkind == "estimated"
    notes: list[str] = []
    if mkind == "estimated":
        notes.append("Token counts use estimate/heuristic path (tiktoken unavailable or strategy=estimate).")
    return TokenizerFitResult(
        case_id=case_id,
        provider_hint=cfg.provider_hint,
        model_hint=cfg.model_hint,
        mode=mode,
        evidence_size=evidence_size,
        tokenizer_strategy=strat,
        measurement_kind=mkind,
        safety_multiplier=cfg.tokenizer_fallback_safety_multiplier,
        estimated_tokens_raw=est,
        measured_tokens_raw=raw_measured,
        budget_injection_cap=max_inj,
        rendered_tokens_after_safety=after_safety,
        fit_pass=fit_pass,
        budget_overage=overage,
        trim_events=trim_events,
        fallback_tokenizer=fallback,
        notes=notes,
    )


def run_tokenizer_matrix(
    base_dir: Path,
    *,
    storage_backend: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
) -> list[TokenizerFitResult]:
    """Exercise provider/model/mode x evidence-size matrix with deterministic synthetic stress text."""
    results: list[TokenizerFitResult] = []
    providers = [None, "openai"]
    models = [None, "gpt-4"]
    modes = ["debugging", "design"]
    sizes: list[EvidenceSize] = ["short", "medium", "long"]
    idx = 0
    for prov in providers:
        for mod in models:
            for mode in modes:
                for sz in sizes:
                    idx += 1
                    ov = dict(config_overrides or {})
                    ov.update(
                        {
                            "provider_hint": prov,
                            "model_hint": mod,
                            "tokenizer_strategy": ov.get("tokenizer_strategy", "auto"),
                            "inject_top_k_routes": 4,
                        }
                    )
                    totals = {"short": 6000, "medium": 9000, "long": 14000}
                    ov["inject_top_k_raw_excerpts"] = 2
                    cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=ov)
                    tf = _fit_tokenizer_case(
                        f"matrix-{idx}",
                        cfg=cfg,
                        mode=mode,
                        evidence_size=sz,
                        total_tokens=totals[sz],
                        id_prefix=f"m_mtx_{idx}",
                        stress_summary=False,
                    )
                    if tf.measurement_kind == "estimated":
                        tf.notes.append("Estimated-only tokenizer path; install tiktoken for measured validation.")
                    results.append(tf)
    return results


def _run_tokenizer_fit_for_config(
    case_id: str,
    *,
    cfg: RoutingConfig,
    mode: str,
    evidence_size: EvidenceSize,
    total_tokens: int = 12000,
) -> TokenizerFitResult:
    return _fit_tokenizer_case(
        case_id,
        cfg=cfg,
        mode=mode,
        evidence_size=evidence_size,
        total_tokens=total_tokens,
        id_prefix=f"syn_{case_id.replace(' ', '_')}",
        stress_summary=True,
    )


def _run_retrieval_case(
    plugin: HermesMemPalaceRoutingPlugin,
    case: EvalCase,
    aliases: Mapping[str, str],
    *,
    baseline_k: int,
) -> tuple[RetrievalQualityResult, bool]:
    ex = case.expect
    k = int(ex.get("recall_k") or plugin.config.inject_top_k_routes)
    expected_ids = _resolve_id_list(ex.get("memory_ids_in_top_k") or ex.get("memory_ids_in_selected"), aliases)
    must_exclude = _resolve_id_list(ex.get("must_exclude_memory_ids"), aliases)
    expected_rooms = [str(x) for x in (ex.get("expected_rooms") or [])]

    payload = plugin.build_context_for_query(
        query=case.query,
        total_tokens=case.total_tokens,
        active_project=case.active_project,
        mode=case.mode,
    )
    ranked: list[RouteCandidate] = payload["route_candidates"]
    selected = [e.memory_id for e in payload["evidence"]]
    selected_rooms = [e.room for e in payload["evidence"]]
    fallback = bool(payload.get("fallback_used"))
    rendered = str(payload.get("rendered_block") or "")
    trace = payload["trace"]
    max_inj = int(trace.token_counts.get("injection_token_cap", 0) or 0)
    strat = plugin.config.tokenizer_strategy
    after_safety = _tok_with_safety(
        rendered,
        model_hint=plugin.config.model_hint,
        provider_hint=plugin.config.provider_hint,
        strategy=strat,
        safety_multiplier=plugin.config.tokenizer_fallback_safety_multiplier,
    )
    fit_pass = after_safety <= max_inj if max_inj > 0 else True

    recall, top1 = _expected_in_top_k(ranked, expected_ids, k)
    wrong = _wrong_room_rate(selected_rooms, expected_rooms) if expected_rooms else 0.0

    conflict_leakage = any(mid in selected for mid in must_exclude)
    raw_diag_included: bool | None = None
    excerpts = list(payload.get("raw_diagnostic_excerpts") or [])
    evidence_objs = list(payload.get("evidence") or [])
    inline_diag_excerpt = any(bool(getattr(ev, "raw_excerpt", None)) for ev in evidence_objs)
    if case.expect_raw_diagnostics is not None:
        raw_diag_included = len(excerpts) > 0 or inline_diag_excerpt

    envelopes = plugin.storage.list_envelopes()
    baseline_text = _legacy_baseline_text(envelopes, baseline_k)
    base_recall = None
    comparison = None
    if expected_ids:
        base_ids = [e.memory_id for e in sorted(envelopes, key=lambda e: e.memory_id)[:baseline_k]]
        base_hits = sum(1 for e in expected_ids if e in base_ids)
        base_recall = base_hits / max(1, len(expected_ids))
        if recall > base_recall:
            comparison = "win"
        elif recall < base_recall:
            comparison = "loss"
        else:
            comparison = "tie"

    mp_block = ex.get("mempalace")
    mcmp_checks: dict[str, Any] = {}
    mcmp_ok: bool | None = None
    if isinstance(mp_block, dict) and mp_block:
        env_rooms = {e.memory_id: e.room for e in plugin.storage.list_envelopes()}
        mcmp_checks, mcmp_ok = _mempalace_retrieval_checks(
            mp_block,
            expect=ex,
            payload=payload,
            aliases=aliases,
            rendered=rendered,
            selected_ids=selected,
            selected_rooms=selected_rooms,
            envelope_rooms=env_rooms,
        )

    metrics = {
        "fallback_used": fallback,
        "routing_disabled": bool(payload.get("routing_disabled")),
        "selected_evidence_ids": selected,
        "tokenizer_measurement_kind": tokenizer_measurement_kind(strat),
        "injection_token_cap": max_inj,
        "rendered_after_safety_tokens": after_safety,
        "baseline_text_preview": baseline_text[:240],
        "validated_internal_only": not (isinstance(mp_block, dict) and mp_block),
        "exercised_mempalace_compatible_semantics": bool(mp_block),
    }
    need_diag = case.expect_raw_diagnostics
    pass_diag = True if need_diag is None else (raw_diag_included is True)
    default_min_recall = 1.0 if expected_ids else 0.0
    min_recall = float(ex.get("min_recall_at_k", default_min_recall))
    internal_pass = (
        not fallback
        and recall >= min_recall
        and wrong <= float(ex.get("max_wrong_room_rate", 1.0))
        and not conflict_leakage
        and pass_diag
        and fit_pass
    )
    res = RetrievalQualityResult(
        case_id=case.id,
        category=case.category,
        recall_at_k=recall,
        top1_correct=top1,
        wrong_room_rate=wrong,
        conflict_leakage=conflict_leakage,
        raw_diag_included=raw_diag_included,
        fit_pass=fit_pass,
        selected_count=len(selected),
        route_candidates_top_ids=[c.memory_id for c in ranked[:k]],
        baseline_recall_at_k=base_recall,
        baseline_comparison=comparison,
        internal_route_pass=internal_pass,
        mempalace_compatible_pass=mcmp_ok,
        mempalace_checks=mcmp_checks,
        metrics=metrics,
    )
    passed = internal_pass if mcmp_ok is None else (internal_pass and mcmp_ok)
    return res, passed


def _run_operational_case(
    base_dir: Path,
    case: EvalCase,
    *,
    storage_backend: str | None,
    config_overrides: Mapping[str, Any] | None,
) -> tuple[OperationalCheckResult, bool]:
    op = case.ops
    name = str(op.get("name") or "noop")
    legacy = storage_backend == "jsonl"
    details: dict[str, Any] = {"op": name}

    work = (base_dir / "_eval_ops" / case.id).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base_dir = work

    if name == "doctor_degraded":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        rep = store.doctor()
        ok = bool(rep.ok)
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=ok,
                legacy_jsonl=legacy,
                details={"doctor_ok": ok, "backend": rep.backend},
                validation_gap=False,
                notes=["doctor on empty/migrated store should be clean; issues indicate degraded state."],
            ),
            ok,
        )

    if name == "fail_open_on_route_error":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        plugin = HermesMemPalaceRoutingPlugin(cfg)

        def boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("forced routing failure")

        real = plugin._build_context_inner  # type: ignore[attr-defined]
        plugin._build_context_inner = boom  # type: ignore[method-assign]
        try:
            payload = plugin.build_context_for_query("hello", 8000, None, "debugging")
        finally:
            plugin._build_context_inner = real  # type: ignore[method-assign]
        fo = bool(payload.get("fallback_used"))
        err = payload.get("error")
        ok = fo and err is not None
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=fo,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"error": err},
                validation_gap=False,
                notes=["Forced routing failure should yield fail-open payload with error string."],
            ),
            ok,
        )

    if name == "missing_raw_file":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        env = store.persist_memory_turn(
            "t1",
            "errors",
            "stacktrace",
            "trace",
            "trace body",
            [],
            None,
            False,
        )
        aid = env.provenance_artifact_ids[0]
        art = store.get_artifact(aid)
        assert art is not None
        Path(art.path).unlink(missing_ok=True)
        plugin = HermesMemPalaceRoutingPlugin(cfg)
        payload = plugin.build_context_for_query("traceback", 12000, None, "debugging")
        # Should not crash; artifact read fails -> dropped or skipped
        ok = payload.get("error") is None
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=bool(payload.get("fallback_used")),
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"fallback_used": payload.get("fallback_used")},
                validation_gap=False,
                notes=["Missing raw on disk should not crash routing; evidence may drop."],
            ),
            ok,
        )

    if name == "route_run_insert_failure_fail_open":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        plugin = HermesMemPalaceRoutingPlugin(cfg)

        calls = {"n": 0}

        def boom_run(_rr: Any) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("forced route_run insert")
            return real_ins(_rr)

        real_ins = plugin.storage.insert_route_run
        plugin.storage.insert_route_run = boom_run  # type: ignore[method-assign]
        try:
            payload = plugin.build_context_for_query("hello world", 12000, None, "debugging")
        finally:
            plugin.storage.insert_route_run = real_ins  # type: ignore[method-assign]
        ok = bool(payload.get("fallback_used"))
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=ok,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"error": payload.get("error")},
                validation_gap=False,
                notes=["insert_route_run failure should fail-open when enabled."],
            ),
            ok,
        )

    if name == "unpin_immediate":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        env = store.persist_memory_turn(
            "t1", "scratch", "note", "x", "body", [], None, False,
            classification_source="rule",
            verification_status="unverified",
            raw_redaction_status="none",
        )
        store.append_pin(env.memory_id, "pin")
        if legacy:
            return (
                OperationalCheckResult(
                    case_id=case.id,
                    check=name,
                    ok=False,
                    fail_open_reported=None,
                    doctor_ok=None,
                    legacy_jsonl=True,
                    details={},
                    validation_gap=True,
                    notes=["unpin requires SQLite backend; skipped as validation gap on JSONL."],
                ),
                True,
            )
        try:
            store.set_memory_pinned(env.memory_id, False, "eval unpin")
        except UnsupportedStorageOperation as exc:
            return (
                OperationalCheckResult(
                    case_id=case.id,
                    check=name,
                    ok=False,
                    fail_open_reported=None,
                    doctor_ok=None,
                    legacy_jsonl=legacy,
                    details={"error": str(exc)},
                    validation_gap=True,
                    notes=[str(exc)],
                ),
                True,
            )
        envs = store.list_envelopes()
        pinned_now = next(e for e in envs if e.memory_id == env.memory_id).pinned
        ok = not pinned_now
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"pinned_after": pinned_now},
                validation_gap=False,
                notes=[],
            ),
            ok,
        )

    if name == "duplicate_aware_filing_sha":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        prov = MemPalaceRoutingProvider(store, cfg)
        raw_body = "MP_EVAL_DUPLICATE_BODY_SHA\nsecond line identical"
        e1 = prov.store_artifact_as_memory(
            "dup_t1", "errors", "note", "summary a", raw_body, [], None, False, fail_open=False
        )
        e2 = prov.store_artifact_as_memory(
            "dup_t2", "errors", "note", "summary b", raw_body, [], None, False, fail_open=False
        )
        ok = e1 is not None and e2 is not None and e1.memory_id == e2.memory_id
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"memory_id": e1.memory_id if e1 else None, "dedupe_same_id": ok},
                validation_gap=False,
                notes=[
                    "MemPalace-style duplicate check: identical drawer raw (SHA) should not create a second drawer "
                    "envelope before add/upsert."
                ],
                mempalace_signal={
                    "tool_surface": "mempalace_check_duplicate",
                    "behavior": "second ingest returns existing memory id",
                },
            ),
            ok,
        )

    if name == "migration_state_mismatch_detected":
        if legacy:
            return (
                OperationalCheckResult(
                    case_id=case.id,
                    check=name,
                    ok=False,
                    fail_open_reported=None,
                    doctor_ok=None,
                    legacy_jsonl=True,
                    details={},
                    validation_gap=True,
                    notes=["SQLite-only: schema migration mismatch detection."],
                ),
                True,
            )
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        dbp = getattr(store, "db_path", None)
        if not dbp:
            return (
                OperationalCheckResult(
                    case_id=case.id,
                    check=name,
                    ok=False,
                    fail_open_reported=None,
                    doctor_ok=None,
                    legacy_jsonl=legacy,
                    details={},
                    validation_gap=True,
                    notes=["No db_path on storage backend."],
                ),
                False,
            )
        exp = expected_migration_versions()
        victim = exp[-1] if exp else ""
        conn = sqlite3.connect(str(dbp))
        try:
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (victim,))
            conn.commit()
        finally:
            conn.close()
        rep = store.doctor()
        mismatch = any("migration" in i.lower() for i in rep.issues)
        ok = not rep.ok and mismatch
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=rep.ok,
                legacy_jsonl=legacy,
                details={
                    "removed_applied_version": victim,
                    "doctor_issues": rep.issues,
                },
                validation_gap=False,
                notes=["Operator should run `hermes-mp migrate` to repair schema drift."],
                mempalace_signal={"backend_expectation": "schema versions match expected set"},
            ),
            ok,
        )

    if name == "repeated_stacktrace_suppression":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        prov = MemPalaceRoutingProvider(store, cfg)
        stack = (
            'Traceback (most recent call last):\n  File "app.py", line 9\n'
            "ValueError: MP_EVAL_REPEAT_SIG"
        )
        e1 = prov.store_artifact_as_memory("rs1", "errors", "stacktrace", "err1", stack, [], None, False)
        e2 = prov.store_artifact_as_memory("rs2", "errors", "stacktrace", "err2", stack, [], None, False)
        ok = e1 is not None and e2 is not None and e1.memory_id == e2.memory_id
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"same_drawer_memory": ok},
                validation_gap=False,
                notes=["Repeated identical diagnostic fingerprint maps to same drawer (ingest path)."],
            ),
            ok,
        )

    if name == "taxonomy_status_stats_signal":
        cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
        store = create_storage(cfg)
        store.persist_memory_turn(
            "tax1",
            "project/demo",
            "note",
            "taxonomy seed",
            "body",
            [],
            None,
            False,
            classification_source="rule",
            verification_status="unverified",
            raw_redaction_status="none",
        )
        s = store.stats()
        room_ok = "project/demo" in (s.rooms or {})
        backend_ok = s.backend == "sqlite"
        ok = backend_ok and room_ok
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={"backend": s.backend, "rooms": dict(s.rooms or {})},
                validation_gap=False,
                notes=[
                    "MemPalace-style taxonomy/status: list rooms and counts (maps to mempalace_list_rooms / "
                    "mempalace_get_taxonomy expectations at the Hermes boundary)."
                ],
                mempalace_signal={
                    "tools": ["mempalace_list_wings", "mempalace_list_rooms", "mempalace_get_taxonomy", "mempalace_status"],
                    "report_shape": "backend + per-room counts",
                },
            ),
            ok,
        )

    if name == "tokenizer_unavailable_explicit":
        cfg = _cfg_for_eval(
            base_dir,
            storage_backend=storage_backend,
            overrides={**(config_overrides or {}), "tokenizer_strategy": "estimate"},
        )
        mk = tokenizer_measurement_kind(cfg.tokenizer_strategy)
        ok = mk == "estimated"
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=ok,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details={
                    "tokenizer_strategy": cfg.tokenizer_strategy,
                    "measurement_kind": mk,
                    "note": "Install tiktoken for measured-token validation in tokenizer-fit matrix.",
                },
                validation_gap=mk != "measured",
                notes=[
                    "When only estimated counts are available, treat token-fit proof as conservative.",
                ],
            ),
            ok,
        )

    if name == "redaction_toggle_report":
        # Informational: both configs validate; report only distinguishes policy intent.
        cfg_mask = _cfg_for_eval(
            base_dir,
            storage_backend=storage_backend,
            overrides={**(config_overrides or {}), "redact_before_persist": True, "redaction_policy": "mask"},
        )
        cfg_none = _cfg_for_eval(
            base_dir,
            storage_backend=storage_backend,
            overrides={**(config_overrides or {}), "redact_before_persist": True, "redaction_policy": "none"},
        )
        details = {
            "mask_policy": cfg_mask.redaction_policy,
            "none_policy": cfg_none.redaction_policy,
            "note": "Redaction path selection is config-driven; persistence tests cover masking behavior.",
        }
        return (
            OperationalCheckResult(
                case_id=case.id,
                check=name,
                ok=True,
                fail_open_reported=None,
                doctor_ok=None,
                legacy_jsonl=legacy,
                details=details,
                validation_gap=False,
                notes=["Compare redaction_policy=mask vs none in operator config; see redaction tests for bytes."],
            ),
            True,
        )

    details["error"] = f"unknown operational check: {name}"
    return (
        OperationalCheckResult(
            case_id=case.id,
            check=name,
            ok=False,
            fail_open_reported=None,
            doctor_ok=None,
            legacy_jsonl=legacy,
            details=details,
            validation_gap=True,
            notes=["Unknown op"],
        ),
        False,
    )


def run_eval_suite(
    fixture: Mapping[str, Any],
    base_dir: Path,
    *,
    storage_backend: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
    run_matrix: bool = True,
    baseline_k: int = 4,
    case_filter: Callable[[EvalCase], bool] | None = None,
) -> EvalSuiteReport:
    """Execute all cases in a loaded fixture against a fresh storage root."""
    suite_id = str(fixture.get("suite_id") or "suite")
    version = int(fixture.get("version") or 1)
    cfg = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=config_overrides)
    store = create_storage(cfg)
    aliases = _seed_storage(store, list(fixture.get("seed") or []))
    _apply_seed_resolutions(store, aliases, list(fixture.get("seed_resolutions") or []))

    results: list[EvalResult] = []
    matrix: list[TokenizerFitResult] = []

    for raw_case in fixture.get("cases") or []:
        case = EvalCase.from_mapping(raw_case)
        if case_filter and not case_filter(case):
            continue
        if case.kind == "retrieval":
            ov = dict(config_overrides or {})
            if case.tokenizer_strategy:
                ov["tokenizer_strategy"] = case.tokenizer_strategy
            if case.provider_hint is not None:
                ov["provider_hint"] = case.provider_hint
            if case.model_hint is not None:
                ov["model_hint"] = case.model_hint
            cfg2 = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=ov)
            plugin = HermesMemPalaceRoutingPlugin(cfg2)
            try:
                r, ok = _run_retrieval_case(plugin, case, aliases, baseline_k=baseline_k)
                results.append(EvalResult(case.id, "retrieval", ok, retrieval=r))
            except Exception as exc:
                results.append(
                    EvalResult(case.id, "retrieval", False, error=f"{type(exc).__name__}: {exc}")
                )
        elif case.kind == "tokenizer_fit":
            ov = dict(config_overrides or {})
            if case.tokenizer_strategy:
                ov["tokenizer_strategy"] = case.tokenizer_strategy
            if case.provider_hint is not None:
                ov["provider_hint"] = case.provider_hint
            if case.model_hint is not None:
                ov["model_hint"] = case.model_hint
            cfg2 = _cfg_for_eval(base_dir, storage_backend=storage_backend, overrides=ov)
            sz: EvidenceSize = case.evidence_size or "medium"
            tf = _run_tokenizer_fit_for_config(
                case.id,
                cfg=cfg2,
                mode=case.mode,
                evidence_size=sz,
                total_tokens=case.total_tokens,
            )
            ok = tf.fit_pass and not tf.budget_overage
            results.append(EvalResult(case.id, "tokenizer_fit", ok, tokenizer=tf))
        elif case.kind == "operational":
            try:
                o, ok = _run_operational_case(
                    base_dir,
                    case,
                    storage_backend=storage_backend,
                    config_overrides=config_overrides,
                )
                results.append(EvalResult(case.id, "operational", ok, operational=o))
            except Exception as exc:
                results.append(
                    EvalResult(case.id, "operational", False, error=f"{type(exc).__name__}: {exc}")
                )
        else:
            results.append(EvalResult(case.id, case.kind, False, error="unknown kind"))

    if run_matrix:
        matrix = run_tokenizer_matrix(base_dir, storage_backend=storage_backend, config_overrides=config_overrides)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    mat_ok = all(m.fit_pass and not m.budget_overage for m in matrix) if matrix else True
    r_with_mcmp = [r for r in results if r.kind == "retrieval" and r.retrieval and r.retrieval.mempalace_compatible_pass is not None]
    mcmp_passed = sum(
        1
        for r in r_with_mcmp
        if r.retrieval and r.retrieval.mempalace_compatible_pass
    )
    internal_only = [r for r in results if r.kind == "retrieval" and r.retrieval and r.retrieval.mempalace_compatible_pass is None]
    functional_failures = sum(1 for r in results if not r.passed and not r.error)
    validation_gaps = sum(
        1
        for r in results
        if r.operational and r.operational.validation_gap
    )
    mcmp_gaps = sum(
        1
        for r in r_with_mcmp
        if r.retrieval and r.retrieval.mempalace_compatible_pass is False
    )
    summary = {
        "cases_total": total,
        "cases_passed": passed,
        "cases_failed": total - passed,
        "tokenizer_matrix_total": len(matrix),
        "tokenizer_matrix_pass": mat_ok,
        "legacy_jsonl_note": "JSONL is legacy/best-effort; some ops checks report validation_gap on JSONL.",
        "internal_route_only_cases": len(internal_only),
        "mempalace_retrieval_cases": len(r_with_mcmp),
        "mempalace_retrieval_passed": mcmp_passed,
        "mempalace_compatibility_gap_cases": mcmp_gaps,
        "functional_failure_cases": functional_failures,
        "operational_validation_gap_cases": validation_gaps,
        "rollout_reporting": {
            "functional_failures": functional_failures,
            "validation_gaps": validation_gaps,
            "mempalace_compatibility_gaps": mcmp_gaps,
        },
    }
    return EvalSuiteReport(
        suite_id=suite_id,
        version=version,
        storage_backend=str(cfg.storage_backend),
        legacy_jsonl=cfg.storage_backend == "jsonl",
        results=results,
        summary=summary,
        tokenizer_matrix=matrix,
    )


def thresholds_pass(
    report: EvalSuiteReport,
    *,
    min_recall_at_k: float = 0.0,
    max_wrong_room_rate: float = 1.0,
    require_fit: bool = True,
    require_matrix: bool = True,
    require_case_pass: bool = True,
) -> tuple[bool, list[str]]:
    """Return (ok, reasons)."""
    reasons: list[str] = []
    if require_case_pass:
        for r in report.results:
            if not r.passed:
                reasons.append(f"{r.case_id}: case failed")
    for r in report.results:
        if r.kind == "retrieval" and r.retrieval:
            rr = r.retrieval
            if rr.recall_at_k < min_recall_at_k:
                reasons.append(f"{r.case_id}: recall_at_k {rr.recall_at_k} < {min_recall_at_k}")
            if rr.wrong_room_rate > max_wrong_room_rate:
                reasons.append(f"{r.case_id}: wrong_room_rate {rr.wrong_room_rate} > {max_wrong_room_rate}")
            if require_fit and not rr.fit_pass:
                reasons.append(f"{r.case_id}: fit_pass false")
            if rr.mempalace_compatible_pass is False:
                reasons.append(f"{r.case_id}: mempalace_compatible_pass false")
        if r.kind == "tokenizer_fit" and r.tokenizer:
            if require_fit and (not r.tokenizer.fit_pass or r.tokenizer.budget_overage):
                reasons.append(f"{r.case_id}: tokenizer fit failure")
    if require_matrix and report.tokenizer_matrix:
        for m in report.tokenizer_matrix:
            if not m.fit_pass or m.budget_overage:
                reasons.append(f"{m.case_id}: matrix tokenizer failure")
    ok = not reasons
    return ok, reasons


def human_summary(report: EvalSuiteReport) -> str:
    s = report.summary
    lines = [
        f"suite={report.suite_id} version={report.version} backend={report.storage_backend} legacy_jsonl={report.legacy_jsonl}",
        f"cases: {s.get('cases_passed')}/{s.get('cases_total')} passed",
        f"tokenizer_matrix: {'PASS' if s.get('tokenizer_matrix_pass') else 'FAIL'} ({s.get('tokenizer_matrix_total')} cells)",
    ]
    if s.get("mempalace_retrieval_cases"):
        lines.append(
            f"mempalace-compatible retrieval: {s.get('mempalace_retrieval_passed')}/"
            f"{s.get('mempalace_retrieval_cases')} passed (wing/room/drawer/verbatim)"
        )
    rr = s.get("rollout_reporting") or {}
    if rr:
        lines.append(
            f"rollout signals: functional_failures={rr.get('functional_failures')} "
            f"validation_gaps={rr.get('validation_gaps')} mempalace_gaps={rr.get('mempalace_compatibility_gaps')}"
        )
    for r in report.results:
        if not r.passed:
            lines.append(f"  FAIL {r.case_id} ({r.kind}): {r.error or 'expectations'}")
    return "\n".join(lines)


def dump_report(report: EvalSuiteReport, path: Path | None) -> None:
    text = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
    if path is None:
        print(text)
    else:
        path.write_text(text, encoding="utf-8")
