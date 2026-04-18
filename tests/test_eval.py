"""Sprint 4 validation harness tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_mempalace_routing.eval import (
    EvalSuiteReport,
    load_fixture_file,
    run_eval_suite,
    run_tokenizer_matrix,
    thresholds_pass,
    tokenizer_measurement_kind,
)


_REPO = Path(__file__).resolve().parents[1]


def test_eval_report_json_stable_keys(tmp_path: Path) -> None:
    fx = load_fixture_file(_REPO / "fixtures/eval/02_tokenizer.json")
    report = run_eval_suite(fx, tmp_path, run_matrix=False)
    raw = json.dumps(report.to_dict(), sort_keys=True)
    data = json.loads(raw)
    assert set(data.keys()) >= {"results", "storage_backend", "suite_id", "summary", "tokenizer_matrix", "version"}
    assert data["suite_id"] == "sprint4-tokenizer"


def test_thresholds_pass_respects_failed_case() -> None:
    from hermes_mempalace_routing.eval import EvalResult, EvalSuiteReport

    r = EvalSuiteReport(
        suite_id="t",
        version=1,
        storage_backend="sqlite",
        legacy_jsonl=False,
        results=[EvalResult("x", "retrieval", False, error="boom")],
        summary={"cases_total": 1, "cases_passed": 0, "cases_failed": 1},
        tokenizer_matrix=[],
    )
    ok, reasons = thresholds_pass(r, require_matrix=False)
    assert ok is False
    assert any("failed" in x for x in reasons)


def test_tokenizer_matrix_all_fit(tmp_path: Path) -> None:
    matrix = run_tokenizer_matrix(tmp_path, storage_backend="sqlite")
    assert len(matrix) == 24
    for m in matrix:
        assert m.measurement_kind in ("measured", "estimated")
        assert m.safety_multiplier >= 1.0
        assert m.budget_overage == (m.rendered_tokens_after_safety > m.budget_injection_cap)


def test_tokenizer_measurement_kind_estimate() -> None:
    assert tokenizer_measurement_kind("estimate") == "estimated"


def test_retrieval_fixture_smoke(tmp_path: Path) -> None:
    fx = load_fixture_file(_REPO / "fixtures/eval/01_retrieval.json")
    report = run_eval_suite(fx, tmp_path, run_matrix=False)
    assert all(r.passed for r in report.results)
    ok, _ = thresholds_pass(report, require_matrix=False)
    assert ok is True


def test_baseline_comparison_shape(tmp_path: Path) -> None:
    fx = load_fixture_file(_REPO / "fixtures/eval/01_retrieval.json")
    report = run_eval_suite(fx, tmp_path, run_matrix=False)
    for r in report.results:
        if r.retrieval:
            assert r.retrieval.baseline_comparison in (None, "win", "tie", "loss")
            assert 0.0 <= r.retrieval.recall_at_k <= 1.0


def test_wrong_room_metric_bounded(tmp_path: Path) -> None:
    fx = load_fixture_file(_REPO / "fixtures/eval/01_retrieval.json")
    report = run_eval_suite(fx, tmp_path, run_matrix=False)
    for r in report.results:
        if r.retrieval:
            assert 0.0 <= r.retrieval.wrong_room_rate <= 1.0


def test_ops_fixture_isolated(tmp_path: Path) -> None:
    fx = load_fixture_file(_REPO / "fixtures/eval/03_ops.json")
    report = run_eval_suite(fx, tmp_path, run_matrix=False)
    assert all(r.passed for r in report.results)


def test_strict_threshold_recall(tmp_path: Path) -> None:
    from hermes_mempalace_routing.eval import EvalResult, EvalSuiteReport, RetrievalQualityResult

    rr = RetrievalQualityResult(
        case_id="c",
        category="x",
        recall_at_k=0.2,
        top1_correct=False,
        wrong_room_rate=0.0,
        conflict_leakage=False,
        raw_diag_included=None,
        fit_pass=True,
        selected_count=1,
        route_candidates_top_ids=[],
        baseline_recall_at_k=None,
        baseline_comparison=None,
    )
    r = EvalSuiteReport(
        suite_id="t",
        version=1,
        storage_backend="sqlite",
        legacy_jsonl=False,
        results=[EvalResult("c", "retrieval", True, retrieval=rr)],
        summary={},
        tokenizer_matrix=[],
    )
    ok, reasons = thresholds_pass(r, min_recall_at_k=0.5, require_matrix=False, require_case_pass=False)
    assert ok is False
    assert any("recall" in x for x in reasons)
