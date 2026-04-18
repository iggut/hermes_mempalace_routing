from pathlib import Path

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.plugin import HermesMemPalaceRoutingPlugin


def _jsonl_config(tmp: Path) -> RoutingConfig:
    return RoutingConfig(base_dir=tmp, storage_backend="jsonl")


def test_pre_model_context_assembly_alias_matches_build_context(tmp_path: Path) -> None:
    plugin = HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path))
    plugin.storage.persist_memory_turn(
        turn_id="t1",
        room="project/hermes",
        fact_type="stacktrace",
        summary="SyntaxError in run_agent.py",
        raw_text="SyntaxError: invalid syntax\n",
        route_tags=["syntaxerror"],
        conflict_key=None,
        pinned=False,
    )

    direct = plugin.build_context_for_query(
        query="why startup fails",
        total_tokens=8000,
        active_project="project/hermes",
        mode="debugging",
    )
    alias = plugin.pre_model_context_assembly(
        query="why startup fails",
        total_tokens=8000,
        active_project="project/hermes",
        mode="debugging",
    )

    assert alias["rendered_block"] == direct["rendered_block"]
    assert alias["trace"].selected_evidence_ids == direct["trace"].selected_evidence_ids
    assert alias["fallback_used"] is False
    assert alias["routing_disabled"] is False


def test_post_turn_artifact_ingestion_alias_persists_raw_artifact(tmp_path: Path) -> None:
    plugin = HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path))

    env = plugin.post_turn_artifact_ingestion(
        turn_id="t2",
        room="errors",
        fact_type="tool_output",
        summary="pytest failed with import error",
        raw_text="ImportError: No module named hermes_mempalace_routing\n",
        route_tags=["pytest", "importerror"],
        conflict_key="ck-1",
        pinned=True,
    )

    assert env is not None
    assert env.room == "errors"
    assert env.fact_type == "tool_output"
    assert env.summary == "pytest failed with import error"
    assert env.route_tags == ["pytest", "importerror"]
    assert env.conflict_key == "ck-1"
    assert env.pinned is True

    art = plugin.storage.get_artifact(env.provenance_artifact_ids[0])
    assert art is not None
    assert art.kind == "tool_output"
    assert plugin.storage.read_artifact_text(art.artifact_id) == "ImportError: No module named hermes_mempalace_routing\n"
