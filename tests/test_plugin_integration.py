from pathlib import Path
from unittest.mock import patch

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.plugin import HermesMemPalaceRoutingPlugin
from hermes_mempalace_routing.storage import StorageReadError


def _jsonl_config(tmp: Path) -> RoutingConfig:
    return RoutingConfig(base_dir=tmp, storage_backend="jsonl")


def test_build_context_success_path(tmp_path: Path) -> None:
    plugin = HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path))
    storage = plugin.storage
    storage.persist_memory_turn(
        turn_id="t1",
        room="project/hermes",
        fact_type="stacktrace",
        summary="SyntaxError in run_agent.py",
        raw_text="SyntaxError: invalid syntax\n",
        route_tags=["syntaxerror"],
        conflict_key=None,
        pinned=False,
    )
    payload = plugin.build_context_for_query(
        query="why startup fails",
        total_tokens=8000,
        active_project="project/hermes",
        mode="debugging",
    )
    assert payload["fallback_used"] is False
    assert payload["routing_disabled"] is False
    assert "[MemPalace routed evidence]" in payload["rendered_block"]
    assert payload["trace"] is not None
    assert payload["trace"].selected_evidence_ids


def test_routing_disabled_no_evidence(tmp_path: Path) -> None:
    cfg = _jsonl_config(tmp_path)
    cfg.enabled = False
    plugin = HermesMemPalaceRoutingPlugin(cfg)
    storage = plugin.storage
    storage.persist_memory_turn(
        turn_id="t1",
        room="scratch",
        fact_type="note",
        summary="x",
        raw_text="y",
        route_tags=[],
        conflict_key=None,
        pinned=False,
    )
    payload = plugin.build_context_for_query("q", 1000, None, "debugging")
    assert payload["routing_disabled"] is True
    assert payload["rendered_block"] == ""
    assert payload["evidence"] == []


def test_replace_flag_disables_pre_model_routing(tmp_path: Path) -> None:
    cfg = _jsonl_config(tmp_path)
    cfg.replace_hermes_summarization = False
    plugin = HermesMemPalaceRoutingPlugin(cfg)
    storage = plugin.storage
    storage.persist_memory_turn(
        turn_id="t1",
        room="project/hermes",
        fact_type="stacktrace",
        summary="SyntaxError in run_agent.py",
        raw_text="SyntaxError: invalid syntax\n",
        route_tags=["syntaxerror"],
        conflict_key=None,
        pinned=False,
    )

    payload = plugin.build_context_for_query(
        query="why startup fails",
        total_tokens=8000,
        active_project="project/hermes",
        mode="debugging",
    )

    assert payload["routing_disabled"] is True
    assert payload["fallback_used"] is False
    assert payload["rendered_block"] == ""
    assert payload["evidence"] == []
    assert payload["route_candidates"] == []
    assert payload["trace"].routing_disabled is True


def test_exception_fail_open_returns_fallback_payload(tmp_path: Path) -> None:
    plugin = HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path))

    def boom(*_a, **_k):
        raise RuntimeError("routing exploded")

    with patch.object(plugin.context_engine, "select_evidence", side_effect=boom):
        payload = plugin.build_context_for_query("q", 4000, None, "debugging")
    assert payload["fallback_used"] is True
    assert payload["rendered_block"] == ""
    assert "exploded" in (payload.get("error") or "")


def test_storage_read_error_fail_open(tmp_path: Path) -> None:
    plugin = HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path))

    def boom(*_a: object, **_k: object) -> None:
        raise StorageReadError("disk unavailable")

    with patch.object(plugin.storage, "list_envelopes_and_conflicts", boom):
        payload = plugin.build_context_for_query("q", 2000, None, "debugging")
    assert payload["fallback_used"] is True


def test_record_turn_artifact_disabled_writes_nothing(tmp_path: Path) -> None:
    cfg = _jsonl_config(tmp_path)
    cfg.enabled = False
    plugin = HermesMemPalaceRoutingPlugin(cfg)

    out = plugin.record_turn_artifact(
        turn_id="t1",
        room="project/hermes",
        fact_type="note",
        summary="x",
        raw_text="y",
        route_tags=[],
        conflict_key=None,
        pinned=False,
    )

    assert out is None
    assert plugin.storage.list_envelopes() == []
