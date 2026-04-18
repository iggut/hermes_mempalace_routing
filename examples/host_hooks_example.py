"""Tiny host-app example for HermesHostHooks.

Run directly with:

    python examples/host_hooks_example.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hermes_mempalace_routing import HermesHostHooks, RoutingConfig


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hermes-mp-example-") as tmp:
        hooks = HermesHostHooks.from_config(
            RoutingConfig(base_dir=Path(tmp), storage_backend="jsonl")
        )

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
