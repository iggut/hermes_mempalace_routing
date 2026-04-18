from pathlib import Path

from hermes_mempalace_routing import HermesHostHooks, RoutingConfig


def test_host_hooks_example_runs(tmp_path: Path, capsys) -> None:
    hooks = HermesHostHooks.from_config(RoutingConfig(base_dir=tmp_path, storage_backend="jsonl"))

    hooks.post_turn_artifact_ingestion(
        turn_id="turn-123",
        room="project/hermes",
        fact_type="stacktrace",
        summary="Hermes startup failed with SyntaxError",
        raw_text='SyntaxError: invalid syntax\n  File "run_agent.py", line 1\n',
        route_tags=["syntaxerror", "startup"],
    )

    payload = hooks.pre_model_context_assembly(
        query="why did Hermes startup fail?",
        total_tokens=8000,
        active_project="hermes",
        mode="debugging",
    )

    print(payload["rendered_block"])
    out = capsys.readouterr().out
    assert "[MemPalace routed evidence]" in out
    assert "Hermes startup failed with SyntaxError" in out
