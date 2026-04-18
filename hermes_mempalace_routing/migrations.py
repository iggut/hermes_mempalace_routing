from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path


class MigrationError(Exception):
    """Raised when migrations cannot be applied or the schema is incompatible."""


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY NOT NULL,
            turn_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            path TEXT NOT NULL,
            mime_type TEXT NOT NULL DEFAULT 'text/plain',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL DEFAULT 1,
            redaction_status TEXT NOT NULL DEFAULT 'none'
        );

        CREATE INDEX IF NOT EXISTS idx_artifacts_turn ON artifacts(turn_id);
        CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at);

        CREATE TABLE IF NOT EXISTS envelopes (
            memory_id TEXT PRIMARY KEY NOT NULL,
            room TEXT NOT NULL,
            route_tags TEXT NOT NULL DEFAULT '[]',
            fact_type TEXT NOT NULL DEFAULT 'note',
            summary TEXT NOT NULL DEFAULT '',
            provenance_artifact_ids TEXT NOT NULL DEFAULT '[]',
            provenance_excerpt TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            pinned INTEGER NOT NULL DEFAULT 0,
            conflict_key TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL DEFAULT 1,
            classification_source TEXT NOT NULL DEFAULT 'rule',
            verification_status TEXT NOT NULL DEFAULT 'unverified'
        );

        CREATE INDEX IF NOT EXISTS idx_envelopes_room ON envelopes(room);
        CREATE INDEX IF NOT EXISTS idx_envelopes_room_created ON envelopes(room, created_at);
        CREATE INDEX IF NOT EXISTS idx_envelopes_conflict ON envelopes(conflict_key);
        CREATE INDEX IF NOT EXISTS idx_envelopes_pinned ON envelopes(pinned);

        CREATE TABLE IF NOT EXISTS pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_pins_memory ON pins(memory_id);

        CREATE TABLE IF NOT EXISTS conflicts (
            conflict_key TEXT PRIMARY KEY NOT NULL,
            room TEXT NOT NULL,
            candidate_memory_ids TEXT NOT NULL DEFAULT '[]',
            resolved_memory_id TEXT,
            resolution_reason TEXT,
            status TEXT NOT NULL DEFAULT 'unresolved',
            resolution_actor TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_conflicts_room ON conflicts(room);

        CREATE TABLE IF NOT EXISTS route_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            query TEXT NOT NULL,
            mode TEXT NOT NULL,
            active_project TEXT,
            route_candidates_json TEXT NOT NULL DEFAULT '[]',
            selected_evidence_ids TEXT NOT NULL DEFAULT '[]',
            dropped_evidence_ids TEXT NOT NULL DEFAULT '[]',
            dropped_reasons_json TEXT NOT NULL DEFAULT '{}',
            token_counts_json TEXT NOT NULL DEFAULT '{}',
            fallback_used INTEGER NOT NULL DEFAULT 0,
            routing_disabled INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_route_runs_created ON route_runs(created_at);
        """
    )


MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("001_initial", _migration_001_initial),
]


def migrate(
    db_path: Path,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Apply pending migrations idempotently; record each version in schema_migrations."""
    own_conn = connection is None
    if own_conn:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
    else:
        conn = connection

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema_migrations_table(conn)

        for version, fn in MIGRATIONS:
            applied = _applied_versions(conn)
            if version in applied:
                continue
            try:
                fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                    (version,),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise MigrationError(
                    f"Migration {version} failed; database may be partially upgraded. "
                    f"Original error: {exc}"
                ) from exc
    finally:
        if own_conn:
            conn.close()


def assert_schema_migrations(db_path: Path) -> None:
    """Verify schema_migrations exists and expected baseline migration is present."""
    if not db_path.is_file():
        raise MigrationError(
            f"SQLite database not found at {db_path}. "
            "Run the application once to initialize, or migrate explicitly."
        )
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if cur.fetchone() is None:
            raise MigrationError(
                f"Database at {db_path} is missing schema_migrations; "
                "run migrations or remove the file to reinitialize."
            )
        applied = _applied_versions(conn)
        if "001_initial" not in applied:
            raise MigrationError(
                f"Database at {db_path} is missing baseline migration 001_initial. "
                "Run migrations or restore from backup."
            )
    finally:
        conn.close()
