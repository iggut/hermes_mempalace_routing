import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    return subprocess.run(
        [sys.executable, "-m", "hermes_mempalace_routing.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_cli_migrate_and_doctor_and_stats(tmp_path: Path) -> None:
    base = ["--base-dir", str(tmp_path)]
    r = _run(*base, "migrate", cwd=tmp_path)
    assert r.returncode == 0
    assert "applied_migrations" in r.stdout
    r2 = _run(*base, "doctor", cwd=tmp_path)
    assert r2.returncode == 0
    r3 = _run(*base, "stats", cwd=tmp_path)
    assert r3.returncode == 0
    data = json.loads(r3.stdout)
    assert data["backend"] == "sqlite"


def test_cli_route_json(tmp_path: Path) -> None:
    base = ["--base-dir", str(tmp_path)]
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    subprocess.run(
        [sys.executable, "-m", "hermes_mempalace_routing.cli", *base, "migrate"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        env=env,
    )
    r = _run(*base, "route", "hello world", "--json", cwd=tmp_path)
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["query"] == "hello world"
    assert "route_candidates" in payload


def test_cli_resolve_and_unpin_roundtrip(tmp_path: Path) -> None:
    from hermes_mempalace_routing.config import RoutingConfig
    from hermes_mempalace_routing.storage_sqlite import SQLiteRoutingStorage

    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite")
    store = SQLiteRoutingStorage(cfg)
    e1 = store.persist_memory_turn(
        "t1", "project/x", "note", "a", "rawa", [], "ck", False,
        classification_source="rule",
        verification_status="unverified",
        raw_redaction_status="none",
    )
    e2 = store.persist_memory_turn(
        "t2", "project/x", "note", "b", "rawb", [], "ck", False,
        classification_source="rule",
        verification_status="unverified",
        raw_redaction_status="none",
    )
    base = ["--base-dir", str(tmp_path)]
    r = _run(
        *base,
        "resolve-conflict",
        "ck",
        "--winner",
        e1.memory_id,
        cwd=tmp_path,
    )
    assert r.returncode == 0
    store.set_memory_pinned(e1.memory_id, True, "pin")
    r2 = _run(*base, "unpin", e1.memory_id, cwd=tmp_path)
    assert r2.returncode == 0
