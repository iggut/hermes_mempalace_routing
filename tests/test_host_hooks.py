from pathlib import Path

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.host_hooks import HermesHostHooks
from hermes_mempalace_routing.plugin import HermesMemPalaceRoutingPlugin


def _jsonl_config(tmp: Path) -> RoutingConfig:
    return RoutingConfig(base_dir=tmp, storage_backend="jsonl")


def test_host_hooks_wire_post_turn_into_next_pre_model_context(tmp_path: Path) -> None:
    hooks = HermesHostHooks(HermesMemPalaceRoutingPlugin(_jsonl_config(tmp_path)))

    persisted = hooks.post_turn_artifact_ingestion(
        turn_id="turn-42",
        room="project/hermes",
        fact_type="stacktrace",
        summary="Hermes startup failed with SyntaxError",
        raw_text="SyntaxError: invalid syntax\n  File \"run_agent.py\", line 1\n",
        route_tags=["syntaxerror", "startup"],
        conflict_key=None,
        pinned=False,
    )

    payload = hooks.pre_model_context_assembly(
        query="why did Hermes startup fail",
        total_tokens=8000,
        active_project="hermes",
        mode="debugging",
    )

    assert persisted is not None
    assert persisted.memory_id in payload["trace"].selected_evidence_ids
    assert persisted.provenance_artifact_ids
    assert payload["fallback_used"] is False
    assert payload["routing_disabled"] is False
    assert "Hermes startup failed with SyntaxError" in payload["rendered_block"]
    assert "SyntaxError: invalid syntax" in payload["rendered_block"]
