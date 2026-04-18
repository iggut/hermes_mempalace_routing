from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.storage import StorageWriteError
from hermes_mempalace_routing.storage_sqlite import SQLiteRoutingStorage


def test_persist_memory_turn_rollback_leaves_no_artifact_row(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated envelope failure")

    with patch.object(SQLiteRoutingStorage, "_insert_envelope_row", boom):
        with pytest.raises(StorageWriteError):
            store.persist_memory_turn(
                turn_id="t1",
                room="scratch",
                fact_type="note",
                summary="s",
                raw_text="body",
                route_tags=[],
                conflict_key=None,
                pinned=False,
            )
    assert store.list_artifacts() == []
    assert store.list_envelopes() == []
    assert not list((tmp_path / "raw").rglob("*.txt"))


def test_insert_route_run_best_effort_no_raise(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    from hermes_mempalace_routing.models import RouteRun

    run = RouteRun(
        query="q",
        mode="debugging",
        active_project=None,
        route_candidates=[],
        selected_evidence_ids=[],
        dropped_evidence_ids=[],
        dropped_reasons={},
        token_counts={},
        fallback_used=False,
    )
    with patch.object(SQLiteRoutingStorage, "_connect", side_effect=RuntimeError("no db")):
        rid = store.insert_route_run(run)
    assert rid == -1
