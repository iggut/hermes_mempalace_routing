from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .config import RoutingConfig
from .migrations import MigrationError, migrate
from .models import ConflictRecord, MemoryEnvelope, RawArtifact, RouteRun
from .storage import IndexCorruptionError, StorageReadError, StorageWriteError


def _json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(s: str) -> object:
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise IndexCorruptionError(f"Invalid JSON in index: {exc}") from exc


class SQLiteRoutingStorage:
    """Production metadata backend with transactional writes."""

    def __init__(self, config: RoutingConfig):
        self._config = config
        self.base_dir = config.base_dir.expanduser()
        self.raw_dir = self.base_dir / "raw"
        self.index_dir = self.base_dir / "index"
        self.cache_dir = self.base_dir / "cache"
        self.db_path = config.resolved_db_path()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            migrate(self.db_path)
        except MigrationError:
            raise
        except Exception as exc:
            raise StorageWriteError(f"Failed to initialize database at {self.db_path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _persist_raw_artifact_in_txn(self, turn_id: str, kind: str, text: str) -> RawArtifact:
        """Write raw file atomically, then caller wraps DB insert in a transaction."""
        now = datetime.now(UTC)
        artifact_id = f"art_{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        day_dir = self.raw_dir / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{artifact_id}_{kind}.txt"
        self._atomic_write_text(path, text)
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RawArtifact(
            artifact_id=artifact_id,
            turn_id=turn_id,
            kind=kind,
            created_at=now.isoformat(),
            path=str(path),
            size_bytes=len(text.encode("utf-8")),
            sha256=sha256,
            redaction_status="none",
        )

    def _insert_artifact_row(self, conn: sqlite3.Connection, raw: RawArtifact) -> None:
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

    def _insert_envelope_row(self, conn: sqlite3.Connection, env: MemoryEnvelope) -> None:
        conn.execute(
            """
            INSERT INTO envelopes (
                memory_id, room, route_tags, fact_type, summary, provenance_artifact_ids,
                provenance_excerpt, confidence, pinned, conflict_key, created_at, updated_at,
                schema_version, classification_source, verification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                env.memory_id,
                env.room,
                _json_dumps(env.route_tags),
                env.fact_type,
                env.summary,
                _json_dumps(env.provenance_artifact_ids),
                env.provenance_excerpt,
                env.confidence,
                1 if env.pinned else 0,
                env.conflict_key,
                env.created_at,
                env.updated_at,
                env.schema_version,
                env.classification_source,
                env.verification_status,
            ),
        )

    def persist_raw_artifact(self, turn_id: str, kind: str, text: str) -> RawArtifact:
        raw = self._persist_raw_artifact_in_txn(turn_id, kind, text)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_artifact_row(conn, raw)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            try:
                Path(raw.path).unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()
        return raw

    def append_envelope(self, env: MemoryEnvelope) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_envelope_row(conn, env)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()

    def append_pin(self, memory_id: str, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO pins (memory_id, reason, created_at, pinned) VALUES (?, ?, ?, 1)",
                (memory_id, reason, now),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()

    def append_conflict(self, conflict: ConflictRecord) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO conflicts (
                    conflict_key, room, candidate_memory_ids, resolved_memory_id, resolution_reason,
                    status, resolution_actor
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.conflict_key,
                    conflict.room,
                    _json_dumps(conflict.candidate_memory_ids),
                    conflict.resolved_memory_id,
                    conflict.resolution_reason,
                    conflict.status,
                    conflict.resolution_actor,
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()

    def insert_route_run(self, run: RouteRun) -> int:
        """Best-effort: failures must not affect callers (e.g. context assembly)."""
        created = datetime.now(UTC).isoformat()
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            cur = conn.execute(
                """
                INSERT INTO route_runs (
                    created_at, query, mode, active_project, route_candidates_json,
                    selected_evidence_ids, dropped_evidence_ids, dropped_reasons_json,
                    token_counts_json, fallback_used, routing_disabled, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    run.query,
                    run.mode,
                    run.active_project,
                    _json_dumps([c.to_dict() for c in run.route_candidates]),
                    _json_dumps(run.selected_evidence_ids),
                    _json_dumps(run.dropped_evidence_ids),
                    _json_dumps(run.dropped_reasons),
                    _json_dumps(run.token_counts),
                    1 if run.fallback_used else 0,
                    1 if run.routing_disabled else 0,
                    run.error,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return -1
        finally:
            if conn is not None:
                conn.close()

    def _row_to_envelope(self, row: sqlite3.Row) -> MemoryEnvelope:
        try:
            route_tags = _json_loads(row["route_tags"])
            prov = _json_loads(row["provenance_artifact_ids"])
        except IndexCorruptionError:
            raise
        if not isinstance(route_tags, list) or not isinstance(prov, list):
            raise IndexCorruptionError("Malformed envelope JSON")
        return MemoryEnvelope(
            memory_id=row["memory_id"],
            room=row["room"],
            route_tags=[str(x) for x in route_tags],
            fact_type=row["fact_type"],
            summary=row["summary"],
            provenance_artifact_ids=[str(x) for x in prov],
            provenance_excerpt=row["provenance_excerpt"],
            confidence=float(row["confidence"]),
            pinned=bool(row["pinned"]),
            conflict_key=row["conflict_key"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            schema_version=int(row["schema_version"] or 1),
            classification_source=row["classification_source"] or "rule",
            verification_status=row["verification_status"] or "unverified",
        )

    def list_envelopes(self) -> list[MemoryEnvelope]:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT * FROM envelopes ORDER BY created_at")
            return [self._row_to_envelope(r) for r in cur.fetchall()]
        except IndexCorruptionError:
            raise
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def list_conflicts(self) -> list[ConflictRecord]:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT * FROM conflicts")
            out: list[ConflictRecord] = []
            for row in cur.fetchall():
                raw_ids = _json_loads(row["candidate_memory_ids"])
                if not isinstance(raw_ids, list):
                    raise IndexCorruptionError("Malformed conflicts.candidate_memory_ids")
                out.append(
                    ConflictRecord(
                        conflict_key=row["conflict_key"],
                        room=row["room"],
                        candidate_memory_ids=[str(x) for x in raw_ids],
                        resolved_memory_id=row["resolved_memory_id"],
                        resolution_reason=row["resolution_reason"],
                        status=row["status"] or "unresolved",
                        resolution_actor=row["resolution_actor"],
                    )
                )
            return out
        except IndexCorruptionError:
            raise
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def list_artifacts(self) -> list[RawArtifact]:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT * FROM artifacts ORDER BY created_at")
            return [
                RawArtifact(
                    artifact_id=row["artifact_id"],
                    turn_id=row["turn_id"],
                    kind=row["kind"],
                    created_at=row["created_at"],
                    path=row["path"],
                    mime_type=row["mime_type"] or "text/plain",
                    size_bytes=int(row["size_bytes"] or 0),
                    sha256=row["sha256"] or "",
                    schema_version=int(row["schema_version"] or 1),
                    redaction_status=row["redaction_status"] or "none",
                )
                for row in cur.fetchall()
            ]
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def get_artifact(self, artifact_id: str) -> RawArtifact | None:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return RawArtifact(
                artifact_id=row["artifact_id"],
                turn_id=row["turn_id"],
                kind=row["kind"],
                created_at=row["created_at"],
                path=row["path"],
                mime_type=row["mime_type"] or "text/plain",
                size_bytes=int(row["size_bytes"] or 0),
                sha256=row["sha256"] or "",
                schema_version=int(row["schema_version"] or 1),
                redaction_status=row["redaction_status"] or "none",
            )
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def read_artifact_text(self, artifact_id: str) -> str | None:
        art = self.get_artifact(artifact_id)
        if art is None:
            return None
        path = Path(art.path)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageReadError(str(exc)) from exc

    def _atomic_write_text(self, final_path: Path, text: str) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = final_path.with_name(final_path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(final_path)
        except OSError as exc:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise StorageWriteError(str(exc)) from exc

    def persist_memory_turn(
        self,
        turn_id: str,
        room: str,
        fact_type: str,
        summary: str,
        raw_text: str,
        route_tags: list[str] | None,
        conflict_key: str | None,
        pinned: bool,
    ) -> MemoryEnvelope:
        route_tags = route_tags or []
        now = datetime.now(UTC)
        artifact_id = f"art_{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        day_dir = self.raw_dir / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{artifact_id}_{fact_type}.txt"
        self._atomic_write_text(path, raw_text)
        text = raw_text
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raw = RawArtifact(
            artifact_id=artifact_id,
            turn_id=turn_id,
            kind=fact_type,
            created_at=now.isoformat(),
            path=str(path),
            size_bytes=len(text.encode("utf-8")),
            sha256=sha256,
            redaction_status="none",
        )
        env_now = now.isoformat()
        env = MemoryEnvelope(
            memory_id=f"mem_{raw.artifact_id}",
            room=room,
            route_tags=route_tags,
            fact_type=fact_type,
            summary=summary,
            provenance_artifact_ids=[raw.artifact_id],
            provenance_excerpt=None,
            confidence=0.9 if fact_type in {"stacktrace", "shell_output", "tool_output"} else 0.7,
            pinned=pinned,
            conflict_key=conflict_key,
            created_at=env_now,
            updated_at=env_now,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_artifact_row(conn, raw)
            self._insert_envelope_row(conn, env)
            if pinned:
                conn.execute(
                    "INSERT INTO pins (memory_id, reason, created_at, pinned) VALUES (?, ?, ?, 1)",
                    (env.memory_id, "created pinned", env_now),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()
        return env
