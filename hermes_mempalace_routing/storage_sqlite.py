from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .config import RoutingConfig
from .migrations import MigrationError, expected_migration_versions, migrate, read_applied_versions
from .models import (
    ConflictRecord,
    DoctorReport,
    MemoryEnvelope,
    RawArtifact,
    ReindexResult,
    RouteRun,
    StorageStats,
)
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
        self._artifact_text_cache: OrderedDict[str, str] = OrderedDict()
        self._artifact_text_cache_max = 256
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
        # Read-heavy defaults: WAL + mmap improve latency without changing query semantics.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-64000")
            conn.execute("PRAGMA mmap_size=268435456")
        except sqlite3.Error:
            pass
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
                    conflict_key, room, candidate_memory_ids, resolved_memory_id, loser_memory_ids,
                    resolution_reason, status, resolution_actor, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.conflict_key,
                    conflict.room,
                    _json_dumps(conflict.candidate_memory_ids),
                    conflict.resolved_memory_id,
                    _json_dumps(conflict.loser_memory_ids or []),
                    conflict.resolution_reason,
                    conflict.status,
                    conflict.resolution_actor,
                    conflict.resolved_at,
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

    def _conflict_from_row(self, row: sqlite3.Row) -> ConflictRecord:
        raw_ids = _json_loads(row["candidate_memory_ids"])
        if not isinstance(raw_ids, list):
            raise IndexCorruptionError("Malformed conflicts.candidate_memory_ids")
        try:
            losers_raw = row["loser_memory_ids"]
        except (KeyError, IndexError):
            losers_raw = "[]"
        try:
            raw_losers = _json_loads(losers_raw)
        except IndexCorruptionError:
            raw_losers = []
        if not isinstance(raw_losers, list):
            raw_losers = []
        try:
            resolved_at = row["resolved_at"]
        except (KeyError, IndexError):
            resolved_at = None
        return ConflictRecord(
            conflict_key=row["conflict_key"],
            room=row["room"],
            candidate_memory_ids=[str(x) for x in raw_ids],
            resolved_memory_id=row["resolved_memory_id"],
            loser_memory_ids=[str(x) for x in raw_losers],
            resolution_reason=row["resolution_reason"],
            status=row["status"] or "unresolved",
            resolution_actor=row["resolution_actor"],
            resolved_at=resolved_at,
        )

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

    def list_envelopes_and_conflicts(self) -> tuple[list[MemoryEnvelope], list[ConflictRecord]]:
        """Single connection: envelopes + conflicts (pre-model routing hot path)."""
        conn = self._connect()
        try:
            cur = conn.execute("SELECT * FROM envelopes ORDER BY created_at")
            envs = [self._row_to_envelope(r) for r in cur.fetchall()]
            cur = conn.execute("SELECT * FROM conflicts")
            conflicts = [self._conflict_from_row(r) for r in cur.fetchall()]
            return envs, conflicts
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
            return [self._conflict_from_row(r) for r in cur.fetchall()]
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
        cached = self._artifact_text_cache.get(artifact_id)
        if cached is not None:
            self._artifact_text_cache.move_to_end(artifact_id)
            return cached
        art = self.get_artifact(artifact_id)
        if art is None:
            return None
        path = Path(art.path)
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageReadError(str(exc)) from exc
        self._artifact_text_cache[artifact_id] = text
        self._artifact_text_cache.move_to_end(artifact_id)
        while len(self._artifact_text_cache) > self._artifact_text_cache_max:
            self._artifact_text_cache.popitem(last=False)
        return text

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
        *,
        classification_source: str = "rule",
        verification_status: str = "unverified",
        raw_redaction_status: str = "none",
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
            redaction_status=raw_redaction_status,
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
            classification_source=classification_source,
            verification_status=verification_status,
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

    def migrate_schema(self) -> tuple[list[str], list[str]]:
        migrate(self.db_path)
        applied = read_applied_versions(self.db_path)
        return (expected_migration_versions(), applied)

    def find_memory_by_artifact_sha256(self, sha256: str) -> MemoryEnvelope | None:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT e.* FROM envelopes e
                JOIN json_each(e.provenance_artifact_ids) AS j
                JOIN artifacts a ON a.artifact_id = j.value
                WHERE a.sha256 = ?
                LIMIT 1
                """,
                (sha256,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self._row_to_envelope(row)
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def set_memory_pinned(self, memory_id: str, pinned: bool, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT memory_id FROM envelopes WHERE memory_id = ?", (memory_id,))
            if cur.fetchone() is None:
                raise StorageWriteError(f"Unknown memory_id: {memory_id}")
            conn.execute(
                "UPDATE envelopes SET pinned = ?, updated_at = ? WHERE memory_id = ?",
                (1 if pinned else 0, now, memory_id),
            )
            conn.execute(
                "INSERT INTO pins (memory_id, reason, created_at, pinned) VALUES (?, ?, ?, ?)",
                (memory_id, reason, now, 1 if pinned else 0),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise StorageWriteError(str(exc)) from exc
        finally:
            conn.close()

    def stats(self) -> StorageStats:
        conn = self._connect()
        try:
            n_env = int(conn.execute("SELECT COUNT(*) FROM envelopes").fetchone()[0])
            n_art = int(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])
            n_conf = int(conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0])
            n_unres = int(
                conn.execute("SELECT COUNT(*) FROM conflicts WHERE status = ?", ("unresolved",)).fetchone()[0]
            )
            n_pin = int(conn.execute("SELECT COUNT(*) FROM envelopes WHERE pinned = 1").fetchone()[0])
            rooms: dict[str, int] = {}
            for row in conn.execute("SELECT room, COUNT(*) FROM envelopes GROUP BY room"):
                rooms[str(row[0])] = int(row[1])
            fts: dict[str, int] = {}
            for row in conn.execute("SELECT fact_type, COUNT(*) FROM envelopes GROUP BY fact_type"):
                fts[str(row[0])] = int(row[1])
            red: dict[str, int] = {}
            for row in conn.execute("SELECT redaction_status, COUNT(*) FROM artifacts GROUP BY redaction_status"):
                red[str(row[0])] = int(row[1])
            return StorageStats(
                backend="sqlite",
                envelopes=n_env,
                artifacts=n_art,
                conflicts=n_conf,
                unresolved_conflicts=n_unres,
                resolved_conflicts=max(0, n_conf - n_unres),
                pinned_envelopes=n_pin,
                rooms=rooms,
                fact_types=fts,
                redaction=red,
                db_path=str(self.db_path),
            )
        except sqlite3.Error as exc:
            raise StorageReadError(str(exc)) from exc
        finally:
            conn.close()

    def doctor(self) -> DoctorReport:
        issues: list[str] = []
        warnings: list[str] = []
        hints: list[str] = []
        exp = expected_migration_versions()
        applied = read_applied_versions(self.db_path)
        if set(exp) != set(applied):
            issues.append(
                f"Schema migration mismatch: expected {sorted(exp)}, applied {sorted(applied)}. Run `hermes-mp migrate`."
            )
        try:
            conn = self._connect()
            required = {"artifacts", "envelopes", "conflicts", "schema_migrations", "pins", "route_runs"}
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            present = {row[0] for row in cur.fetchall()}
            missing = sorted(required - present)
            if missing:
                issues.append(f"Missing tables: {missing}")
            conn.close()
        except sqlite3.DatabaseError as exc:
            issues.append(f"SQLite open/query failed (possible corruption): {exc}")
            return DoctorReport(
                ok=False,
                backend="sqlite",
                db_path=str(self.db_path),
                schema_versions_expected=exp,
                schema_versions_applied=applied,
                issues=issues,
                warnings=warnings,
                hints=hints + ["Try `hermes-mp migrate` or restore metadata.db from backup."],
            )

        for art in self.list_artifacts():
            p = Path(art.path)
            if not p.is_file():
                issues.append(f"Missing raw artifact file for {art.artifact_id}: {art.path}")

        if not issues:
            hints.append("No blocking issues detected for SQLite metadata and raw artifacts.")
        return DoctorReport(
            ok=not issues,
            backend="sqlite",
            db_path=str(self.db_path),
            schema_versions_expected=exp,
            schema_versions_applied=applied,
            issues=issues,
            warnings=warnings,
            hints=hints,
        )

    def reindex_from_raw(self, *, dry_run: bool = True) -> ReindexResult:
        notes: list[str] = []
        errors: list[str] = []
        inserted = 0
        touched = 0
        skipped = 0
        for path in sorted(self.raw_dir.rglob("*.txt")):
            if path.name.endswith(".tmp"):
                continue
            stem = path.stem
            if "_" not in stem:
                continue
            artifact_id, kind = stem.rsplit("_", 1)
            if not artifact_id.startswith("art_"):
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect()
                cur = conn.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,))
                if cur.fetchone() is not None:
                    skipped += 1
                    continue
                touched += 1
                if dry_run:
                    inserted += 1
                    continue
                now = datetime.now(UTC).isoformat()
                raw = RawArtifact(
                    artifact_id=artifact_id,
                    turn_id="reindex",
                    kind=kind,
                    created_at=now,
                    path=str(path),
                    size_bytes=len(body.encode("utf-8")),
                    sha256=sha,
                    redaction_status="none",
                )
                env = MemoryEnvelope(
                    memory_id=f"mem_{artifact_id}",
                    room="errors",
                    route_tags=[],
                    fact_type=kind,
                    summary="reindexed from raw",
                    provenance_artifact_ids=[artifact_id],
                    provenance_excerpt=None,
                    confidence=0.5,
                    pinned=False,
                    conflict_key=None,
                    created_at=now,
                    updated_at=now,
                    classification_source="import",
                )
                conn.execute("BEGIN IMMEDIATE")
                self._insert_artifact_row(conn, raw)
                self._insert_envelope_row(conn, env)
                conn.commit()
                inserted += 1
            except Exception as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                errors.append(f"{path}: {exc}")
            finally:
                if conn is not None:
                    conn.close()

        notes.append(
            f"Scanned raw tree; inserted={inserted}, skipped_existing={skipped}, touched_files={touched}."
        )
        return ReindexResult(
            dry_run=dry_run,
            backend="sqlite",
            envelopes_inserted=inserted,
            artifacts_touched=touched,
            skipped=skipped,
            errors=errors,
            notes=notes,
        )
