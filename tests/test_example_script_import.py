from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_host_hooks_example_runs_without_install(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [sys.executable, "examples/host_hooks_example.py"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
