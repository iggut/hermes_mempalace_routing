from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import RoutingConfig
from .conflicts import list_conflicts, resolve_conflict
from .eval import (
    EvalCase,
    EvalResult,
    EvalSuiteReport,
    default_fixtures_dir,
    dump_report,
    human_summary,
    load_fixture_dir,
    load_fixture_file,
    run_eval_suite,
    run_tokenizer_matrix,
    thresholds_pass,
)
from .plugin import HermesMemPalaceRoutingPlugin
from .storage import UnsupportedStorageOperation, create_storage


def _config_from_args(args: argparse.Namespace) -> RoutingConfig:
    base_dir = Path(args.base_dir).expanduser() if getattr(args, "base_dir", None) else RoutingConfig.default().base_dir
    cfg = RoutingConfig(base_dir=base_dir)
    if getattr(args, "storage", None):
        object.__setattr__(cfg, "storage_backend", args.storage)
    cfg.validate()
    return cfg


def _plugin_from_args(args: argparse.Namespace) -> HermesMemPalaceRoutingPlugin:
    return HermesMemPalaceRoutingPlugin(_config_from_args(args))


def _storage_from_args(args: argparse.Namespace):
    return create_storage(_config_from_args(args))


def cmd_route(args) -> int:
    plugin = _plugin_from_args(args)
    payload = plugin.build_context_for_query(
        query=args.query,
        total_tokens=args.total_tokens,
        active_project=args.active_project,
        mode=args.mode,
    )
    if getattr(args, "json", False):
        out = {
            "query": args.query,
            "mode": args.mode,
            "active_project": args.active_project,
            "budget": {
                "total_tokens": payload["budget"].total_tokens,
                "live_conversation": payload["budget"].live_conversation,
                "routed_memory": payload["budget"].routed_memory,
                "raw_diagnostics": payload["budget"].raw_diagnostics,
                "reserve": payload["budget"].reserve,
                "remainder": payload["budget"].remainder,
            },
            "route_candidates": [c.to_dict() for c in payload["route_candidates"]],
            "selected_evidence_ids": [e.memory_id for e in payload["evidence"]],
            "dropped_evidence_ids": list(payload["trace"].dropped_evidence_ids),
            "dropped_reasons": dict(payload["trace"].dropped_reasons),
            "fallback_used": payload["fallback_used"],
            "token_counts": dict(payload["trace"].token_counts),
            "routing_disabled": payload["routing_disabled"],
            "error": payload.get("error"),
        }
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0 if not payload.get("error") else 1
    print(f"Active project: {args.active_project or 'none'}")
    print(f"Mode: {args.mode}")
    print(f"Budget: {payload['budget']}")
    print("Route ranking (score, rationale):")
    for cand in payload["route_candidates"][:15]:
        print(f"  {cand.memory_id} score={cand.score:.3f} room={cand.room} rationale={cand.rationale}")
    print(payload["rendered_block"])
    return 0


def cmd_inspect(args) -> int:
    plugin = _plugin_from_args(args)
    envelopes = plugin.storage.list_envelopes()

    if args.target_type == "room":
        matches = [env for env in envelopes if env.room == args.target_id]
        print(f"Room: {args.target_id}")
        for env in matches:
            print(f"- {env.memory_id}: {env.summary}")
        return 0

    if args.target_type == "memory":
        for env in envelopes:
            if env.memory_id == args.target_id:
                print(env)
                return 0
        print("memory not found")
        return 1

    art = plugin.storage.get_artifact(args.target_id)
    if art is not None:
        print(art)
        body = plugin.storage.read_artifact_text(args.target_id)
        if body is not None:
            print("--- raw (stored, utf-8) ---")
            print(body)
        return 0
    for env in envelopes:
        if args.target_id in env.provenance_artifact_ids:
            print(env)
            return 0
    print("artifact not found")
    return 1


def cmd_pin(args) -> int:
    plugin = _plugin_from_args(args)
    plugin.storage.append_pin(args.memory_id, args.reason)
    print(f"Pinned {args.memory_id}: {args.reason}")
    return 0


def cmd_unpin(args) -> int:
    plugin = _plugin_from_args(args)
    try:
        plugin.storage.set_memory_pinned(args.memory_id, False, args.reason or "operator unpin")
    except UnsupportedStorageOperation as exc:
        print(str(exc))
        return 2
    print(f"Unpinned {args.memory_id}")
    return 0


def cmd_conflicts(args) -> int:
    plugin = _plugin_from_args(args)
    envelopes = plugin.storage.list_envelopes()
    if args.room:
        envelopes = [env for env in envelopes if env.room == args.room]
    cfg = plugin.config
    merged = list_conflicts(envelopes, cfg, stored=plugin.storage.list_conflicts())
    if not merged:
        print("No conflicts detected")
        return 0
    for conflict in merged:
        eff = conflict.resolved_memory_id or "(none)"
        print(
            f"{conflict.conflict_key} status={conflict.status} "
            f"effective_winner={eff} candidates={conflict.candidate_memory_ids} "
            f"losers={getattr(conflict, 'loser_memory_ids', [])}"
        )
    return 0


def cmd_resolve_conflict(args) -> int:
    plugin = _plugin_from_args(args)
    envs = plugin.storage.list_envelopes()
    rec = resolve_conflict(
        conflict_key=args.conflict_key,
        winner_memory_id=args.winner,
        actor=args.actor,
        reason=args.reason,
        envelopes=envs,
    )
    plugin.storage.append_conflict(rec)
    print(f"Resolved {args.conflict_key} -> {args.winner} ({rec.status})")
    return 0


def cmd_doctor(args) -> int:
    store = _storage_from_args(args)
    report = store.doctor()
    for line in report.summary_lines():
        print(line)
    return 0 if report.ok else 1


def cmd_migrate(args) -> int:
    store = _storage_from_args(args)
    try:
        before, after = store.migrate_schema()
    except UnsupportedStorageOperation as exc:
        print(str(exc))
        return 2
    print(f"expected_migrations={before}")
    print(f"applied_migrations={after}")
    return 0


def cmd_reindex(args) -> int:
    store = _storage_from_args(args)
    dry = not getattr(args, "apply", False)
    res = store.reindex_from_raw(dry_run=dry)
    print(json.dumps(asdict(res), ensure_ascii=False, sort_keys=True))
    return 0 if not res.errors else 1


def cmd_stats(args) -> int:
    store = _storage_from_args(args)
    s = store.stats()
    print(json.dumps(asdict(s), ensure_ascii=False, sort_keys=True))
    return 0


def _load_eval_fixture(args: argparse.Namespace) -> dict:
    if getattr(args, "fixtures", None):
        p = Path(args.fixtures).expanduser()
        if p.is_file():
            return load_fixture_file(p)
        if p.is_dir():
            return load_fixture_dir(p)
        print(f"eval: fixtures path not found: {p}", file=sys.stderr)
        return {}
    root = default_fixtures_dir()
    if not root.is_dir():
        print(f"eval: default fixtures directory missing: {root}", file=sys.stderr)
        return {}
    return load_fixture_dir(root)


def cmd_eval_run(args: argparse.Namespace) -> int:
    fx = _load_eval_fixture(args)
    if not fx:
        return 2
    base = Path(args.base_dir).expanduser()
    storage = args.storage if getattr(args, "storage", None) else None
    report = run_eval_suite(
        fx,
        base,
        storage_backend=storage,
        run_matrix=not getattr(args, "no_matrix", False),
        baseline_k=int(getattr(args, "baseline_k", 4) or 4),
    )
    ok_thr, thr_reasons = thresholds_pass(
        report,
        min_recall_at_k=float(args.min_recall_at_k),
        max_wrong_room_rate=float(args.max_wrong_room_rate),
        require_matrix=not getattr(args, "no_matrix", False),
        require_case_pass=True,
    )
    if getattr(args, "json", False):
        out = report.to_dict()
        out["thresholds_ok"] = ok_thr
        out["threshold_reasons"] = thr_reasons
        text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2)
        if getattr(args, "output", None):
            Path(args.output).expanduser().write_text(text, encoding="utf-8")
        else:
            print(text)
    else:
        print(human_summary(report))
        if thr_reasons:
            print("Threshold failures:")
            for line in thr_reasons:
                print(f"  - {line}")
        if getattr(args, "output", None) and not getattr(args, "json", False):
            dump_report(report, Path(args.output).expanduser())
    strict = bool(getattr(args, "strict", False))
    all_pass = all(r.passed for r in report.results) and report.summary.get("tokenizer_matrix_pass", True)
    if strict:
        return 0 if (all_pass and ok_thr) else 1
    return 0 if all_pass else 1


def _case_kind_filter(kind: str):
    def _f(c: EvalCase) -> bool:
        return c.kind == kind

    return _f


def cmd_eval_tokenizer_fit(args: argparse.Namespace) -> int:
    base = Path(args.base_dir).expanduser()
    storage = args.storage if getattr(args, "storage", None) else None
    matrix = run_tokenizer_matrix(base, storage_backend=storage)
    mat_ok = all(m.fit_pass and not m.budget_overage for m in matrix)
    fx = _load_eval_fixture(args)
    results_extra: list[EvalResult] = []
    if fx:
        rpt = run_eval_suite(
            fx,
            base,
            storage_backend=storage,
            run_matrix=False,
            case_filter=_case_kind_filter("tokenizer_fit"),
        )
        results_extra = rpt.results
    er: list[EvalResult] = [
        EvalResult(m.case_id, "tokenizer_fit", m.fit_pass and not m.budget_overage, tokenizer=m)
        for m in matrix
    ]
    er.extend(results_extra)
    report = EvalSuiteReport(
        suite_id="tokenizer-fit",
        version=1,
        storage_backend=str(storage or "sqlite"),
        legacy_jsonl=storage == "jsonl",
        results=er,
        summary={
            "cases_total": len(er),
            "cases_passed": sum(1 for r in er if r.passed),
            "cases_failed": sum(1 for r in er if not r.passed),
            "tokenizer_matrix_total": len(matrix),
            "tokenizer_matrix_pass": mat_ok,
        },
        tokenizer_matrix=matrix,
    )
    ok_thr, thr_reasons = thresholds_pass(report, require_matrix=True, require_case_pass=True)
    if getattr(args, "json", False):
        out = report.to_dict()
        out["thresholds_ok"] = ok_thr
        out["threshold_reasons"] = thr_reasons
        text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2)
        if getattr(args, "output", None):
            Path(args.output).expanduser().write_text(text, encoding="utf-8")
        else:
            print(text)
    else:
        print(human_summary(report))
        if thr_reasons:
            print("Threshold failures:")
            for line in thr_reasons:
                print(f"  - {line}")
    strict = bool(getattr(args, "strict", False))
    all_pass = all(r.passed for r in er) and mat_ok
    if strict:
        return 0 if (all_pass and ok_thr) else 1
    return 0 if all_pass else 1


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    fx = _load_eval_fixture(args)
    if not fx:
        return 2
    base = Path(args.base_dir).expanduser()
    storage = args.storage if getattr(args, "storage", None) else None
    report = run_eval_suite(
        fx,
        base,
        storage_backend=storage,
        run_matrix=False,
        case_filter=_case_kind_filter("retrieval"),
    )
    ok_thr, thr_reasons = thresholds_pass(
        report,
        min_recall_at_k=float(args.min_recall_at_k),
        max_wrong_room_rate=float(args.max_wrong_room_rate),
        require_matrix=False,
        require_case_pass=True,
    )
    if getattr(args, "json", False):
        out = report.to_dict()
        out["thresholds_ok"] = ok_thr
        out["threshold_reasons"] = thr_reasons
        text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2)
        if getattr(args, "output", None):
            Path(args.output).expanduser().write_text(text, encoding="utf-8")
        else:
            print(text)
    else:
        print(human_summary(report))
        if thr_reasons:
            print("Threshold failures:")
            for line in thr_reasons:
                print(f"  - {line}")
    strict = bool(getattr(args, "strict", False))
    all_pass = all(r.passed for r in report.results)
    if strict:
        return 0 if (all_pass and ok_thr) else 1
    return 0 if all_pass else 1


def cmd_eval_ops(args: argparse.Namespace) -> int:
    fx = _load_eval_fixture(args)
    if not fx:
        return 2
    base = Path(args.base_dir).expanduser()
    storage = args.storage if getattr(args, "storage", None) else None
    report = run_eval_suite(
        fx,
        base,
        storage_backend=storage,
        run_matrix=False,
        case_filter=_case_kind_filter("operational"),
    )
    ok_thr, thr_reasons = thresholds_pass(report, require_matrix=False, require_case_pass=True)
    if getattr(args, "json", False):
        out = report.to_dict()
        out["thresholds_ok"] = ok_thr
        out["threshold_reasons"] = thr_reasons
        text = json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2)
        if getattr(args, "output", None):
            Path(args.output).expanduser().write_text(text, encoding="utf-8")
        else:
            print(text)
    else:
        print(human_summary(report))
        if thr_reasons:
            print("Threshold failures:")
            for line in thr_reasons:
                print(f"  - {line}")
    strict = bool(getattr(args, "strict", False))
    all_pass = all(r.passed for r in report.results)
    if strict:
        return 0 if (all_pass and ok_thr) else 1
    return 0 if all_pass else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-mp")
    parser.add_argument("--base-dir", default=str(RoutingConfig.default().base_dir))
    parser.add_argument(
        "--storage",
        choices=["sqlite", "jsonl"],
        default=None,
        help="Override storage backend (default: sqlite)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_route = sub.add_parser("route")
    p_route.add_argument("query")
    p_route.add_argument("--active-project")
    p_route.add_argument("--mode", choices=["debugging", "design"], default="debugging")
    p_route.add_argument("--total-tokens", type=int, default=12000)
    p_route.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_route.set_defaults(func=cmd_route)

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("target_type", choices=["room", "memory", "artifact"])
    p_inspect.add_argument("target_id")
    p_inspect.set_defaults(func=cmd_inspect)

    p_pin = sub.add_parser("pin")
    p_pin.add_argument("memory_id")
    p_pin.add_argument("--reason", default="operator pin")
    p_pin.set_defaults(func=cmd_pin)

    p_unpin = sub.add_parser("unpin")
    p_unpin.add_argument("memory_id")
    p_unpin.add_argument("--reason", default="operator unpin")
    p_unpin.set_defaults(func=cmd_unpin)

    p_conflicts = sub.add_parser("conflicts")
    p_conflicts.add_argument("--room")
    p_conflicts.set_defaults(func=cmd_conflicts)

    p_resolve = sub.add_parser("resolve-conflict")
    p_resolve.add_argument("conflict_key")
    p_resolve.add_argument("--winner", required=True)
    p_resolve.add_argument("--actor", default="operator")
    p_resolve.add_argument("--reason", default="explicit_resolution")
    p_resolve.set_defaults(func=cmd_resolve_conflict)

    p_doctor = sub.add_parser("doctor")
    p_doctor.set_defaults(func=cmd_doctor)

    p_migrate = sub.add_parser("migrate")
    p_migrate.set_defaults(func=cmd_migrate)

    p_reindex = sub.add_parser("reindex")
    p_reindex.add_argument(
        "--apply",
        action="store_true",
        help="Actually write rebuilt rows (default is dry-run)",
    )
    p_reindex.set_defaults(func=cmd_reindex)

    p_stats = sub.add_parser("stats")
    p_stats.set_defaults(func=cmd_stats)

    eval_common = argparse.ArgumentParser(add_help=False)
    eval_common.add_argument(
        "--fixtures",
        help="Fixture JSON/YAML file or directory (default: <repo>/fixtures/eval if present)",
    )
    eval_common.add_argument("--json", action="store_true", help="Emit JSON report (stable, sorted keys)")
    eval_common.add_argument("--output", "-o", help="Write JSON report to file (implies structured output)")
    eval_common.add_argument(
        "--strict",
        action="store_true",
        help="Nonzero exit if any case fails or global thresholds fail",
    )
    eval_common.add_argument(
        "--min-recall-at-k",
        type=float,
        default=0.0,
        help="Minimum recall@k across retrieval cases (threshold pass, default 0)",
    )
    eval_common.add_argument(
        "--max-wrong-room-rate",
        type=float,
        default=1.0,
        help="Maximum wrong-room rate for retrieval cases (default 1.0 = disabled)",
    )
    eval_common.add_argument(
        "--no-matrix",
        action="store_true",
        help="For 'eval run': skip provider/model tokenizer matrix (faster)",
    )
    eval_common.add_argument(
        "--baseline-k",
        type=int,
        default=4,
        help="Legacy baseline compares against first-k envelopes by memory_id order",
    )

    p_eval = sub.add_parser("eval", help="Sprint 4 validation suites (tokenizer, retrieval, ops)")
    eval_sub = p_eval.add_subparsers(dest="eval_cmd", required=True)

    p_validate = sub.add_parser(
        "validate",
        help="Alias for `eval` (same subcommands: run, tokenizer-fit, retrieval, ops)",
    )
    validate_sub = p_validate.add_subparsers(dest="validate_cmd", required=True)

    p_er = eval_sub.add_parser("run", parents=[eval_common], help="Run all case kinds + tokenizer matrix")
    p_er.set_defaults(func=cmd_eval_run)

    p_et = eval_sub.add_parser("tokenizer-fit", parents=[eval_common], help="Tokenizer matrix + tokenizer_fit cases")
    p_et.set_defaults(func=cmd_eval_tokenizer_fit)

    p_ee = eval_sub.add_parser("retrieval", parents=[eval_common], help="Retrieval cases only")
    p_ee.set_defaults(func=cmd_eval_retrieval)

    p_eo = eval_sub.add_parser("ops", parents=[eval_common], help="Operational / degraded-path cases")
    p_eo.set_defaults(func=cmd_eval_ops)

    pv_run = validate_sub.add_parser("run", parents=[eval_common], help="Run all case kinds + tokenizer matrix")
    pv_run.set_defaults(func=cmd_eval_run)
    pv_tok = validate_sub.add_parser(
        "tokenizer-fit", parents=[eval_common], help="Tokenizer matrix + tokenizer_fit cases"
    )
    pv_tok.set_defaults(func=cmd_eval_tokenizer_fit)
    pv_ret = validate_sub.add_parser("retrieval", parents=[eval_common], help="Retrieval cases only")
    pv_ret.set_defaults(func=cmd_eval_retrieval)
    pv_ops = validate_sub.add_parser("ops", parents=[eval_common], help="Operational / degraded-path cases")
    pv_ops.set_defaults(func=cmd_eval_ops)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
