from pathlib import Path
import sqlite3

import pytest

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.migrations import MigrationError, assert_schema_migrations, migrate
from hermes_mempalace_routing.storage_sqlite import SQLiteRoutingStorage


def test_migrate_initializes_database(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    migrate(db)
    assert db.is_file()
    assert_schema_migrations(db)


def test_sqlite_persist_memory_turn_transactional_round_trip(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    env = store.persist_memory_turn(
        turn_id="t1",
        room="project/demo",
        fact_type="note",
        summary="hello",
        raw_text="exact body",
        route_tags=["a"],
        conflict_key=None,
        pinned=False,
    )
    arts = store.list_artifacts()
    envs = store.list_envelopes()
    assert len(arts) == 1
    assert len(envs) == 1
    assert envs[0].memory_id == env.memory_id
    assert store.read_artifact_text(arts[0].artifact_id) == "exact body"


def test_read_artifact_text_missing_file_returns_none(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    env = store.persist_memory_turn(
        turn_id="t1",
        room="scratch",
        fact_type="note",
        summary="x",
        raw_text="body",
        route_tags=[],
        conflict_key=None,
        pinned=False,
    )
    aid = env.provenance_artifact_ids[0]
    art = store.get_artifact(aid)
    assert art is not None
    Path(art.path).unlink()
    assert store.read_artifact_text(aid) is None


def test_artifact_primary_key_enforced(tmp_path: Path) -> None:
    cfg = RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=tmp_path / "m.db")
    store = SQLiteRoutingStorage(cfg)
    raw = store.persist_raw_artifact("t", "note", "one")
    conn = sqlite3.connect(str(store.db_path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, turn_id, kind, created_at, path, mime_type, size_bytes, sha256,
                    schema_version, redaction_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw.artifact_id,
                    raw.turn_id,
                    raw.kind,
                    raw.created_at,
                    raw.path,
                    raw.mime_type,
                    raw.size_bytes,
                    raw.sha256,
                    raw.schema_version,
                    raw.redaction_status,
                ),
            )
            conn.commit()
    finally:
        conn.close()


def test_migrate_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    migrate(db)
    migrate(db)
    assert_schema_migrations(db)


def test_assert_schema_migrations_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MigrationError):
        assert_schema_migrations(tmp_path / "nope.db")
