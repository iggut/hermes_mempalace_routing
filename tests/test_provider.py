from pathlib import Path

import pytest

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.provider import ArtifactValidationError, MemPalaceRoutingProvider
from hermes_mempalace_routing.storage_sqlite import SQLiteRoutingStorage


def test_deterministic_stacktrace_classification(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    prov = MemPalaceRoutingProvider(store, cfg)
    raw = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nZeroDivisionError'
    env = prov.store_artifact_as_memory(
        turn_id="t1",
        room="errors",
        fact_type="other",
        summary="err",
        raw_text=raw,
        fail_open=False,
    )
    assert env is not None
    assert env.fact_type == "stacktrace"


def test_duplicate_suppression_by_sha(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    prov = MemPalaceRoutingProvider(store, cfg)
    body = "same body"
    e1 = prov.store_artifact_as_memory(
        turn_id="t1",
        room="scratch",
        fact_type="note",
        summary="a",
        raw_text=body,
        fail_open=False,
    )
    e2 = prov.store_artifact_as_memory(
        turn_id="t2",
        room="scratch",
        fact_type="note",
        summary="b",
        raw_text=body,
        fail_open=False,
    )
    assert e1 is not None
    assert e2 is not None
    assert e1.memory_id == e2.memory_id


def test_repeated_error_grouping_suppresses_second(tmp_path: Path) -> None:
    cfg = RoutingConfig(
        base_dir=tmp_path,
        storage_backend="sqlite",
        db_path=tmp_path / "m.db",
        repeat_error_group_window=50,
    )
    store = SQLiteRoutingStorage(cfg)
    prov = MemPalaceRoutingProvider(store, cfg)
    raw = "Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: boom"
    e1 = prov.store_artifact_as_memory(
        turn_id="t1",
        room="errors",
        fact_type="stacktrace",
        summary="s",
        raw_text=raw,
        fail_open=False,
    )
    e2 = prov.store_artifact_as_memory(
        turn_id="t2",
        room="errors",
        fact_type="stacktrace",
        summary="s2",
        raw_text=raw,
        fail_open=False,
    )
    assert e1 is not None
    assert e2 is not None
    assert e1.memory_id == e2.memory_id


def test_invalid_empty_room_fail_open(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    prov = MemPalaceRoutingProvider(store, cfg)
    assert prov.store_artifact_as_memory(
        turn_id="t",
        room="  ",
        fact_type="note",
        summary="x",
        raw_text="y",
        fail_open=True,
    ) is None


def test_invalid_empty_room_strict_raises(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    prov = MemPalaceRoutingProvider(store, cfg)
    with pytest.raises(ArtifactValidationError):
        prov.store_artifact_as_memory(
            turn_id="t",
            room="  ",
            fact_type="note",
            summary="x",
            raw_text="y",
            fail_open=False,
        )
