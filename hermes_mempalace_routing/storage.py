from __future__ import annotations

from typing import Protocol
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .config import RoutingConfig
from .migrations import MIGRATIONS
from .models import (
    ConflictRecord,
    DoctorReport,
    MemoryEnvelope,
    RawArtifact,
    ReindexResult,
    RouteRun,
    StorageStats,
)


class StorageError(Exception):
    """Base class for storage failures."""


class UnsupportedStorageOperation(StorageError):
    """Backend does not support this operation (e.g. JSONL legacy)."""


class StorageWriteError(StorageError):
    """Raised when a durable write fails before being safely visible."""


class StorageReadError(StorageError):
    """Raised when a read fails (I/O or decode)."""


class IndexCorruptionError(StorageError):
    """Raised when index metadata cannot be decoded or is internally inconsistent."""


class StorageBackend(Protocol):
    """Pluggable persistence for routing metadata and raw artifact paths."""

    base_dir: Path
    raw_dir: Path
    index_dir: Path

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
    ) -> MemoryEnvelope: ...

    def persist_raw_artifact(self, turn_id: str, kind: str, text: str) -> RawArtifact: ...

    def append_envelope(self, env: MemoryEnvelope) -> None: ...

    def append_pin(self, memory_id: str, reason: str) -> None: ...

    def append_conflict(self, conflict: ConflictRecord) -> None: ...

    def insert_route_run(self, run: RouteRun) -> int: ...

    def list_envelopes(self) -> list[MemoryEnvelope]: ...

    def list_conflicts(self) -> list[ConflictRecord]: ...

    def list_envelopes_and_conflicts(self) -> tuple[list[MemoryEnvelope], list[ConflictRecord]]: ...

    def list_artifacts(self) -> list[RawArtifact]: ...

    def get_artifact(self, artifact_id: str) -> RawArtifact | None: ...

    def read_artifact_text(self, artifact_id: str) -> str | None: ...

    def doctor(self) -> DoctorReport: ...

    def migrate_schema(self) -> tuple[list[str], list[str]]:
        """Return (expected_migration_versions, applied_versions)."""

    def stats(self) -> StorageStats: ...

    def reindex_from_raw(self, *, dry_run: bool = True) -> ReindexResult: ...

    def set_memory_pinned(self, memory_id: str, pinned: bool, reason: str) -> None: ...

    def find_memory_by_artifact_sha256(self, sha256: str) -> MemoryEnvelope | None: ...


def create_storage(config: RoutingConfig) -> StorageBackend:
    """Production entry point for storage (SQLite default, JSONL legacy). Prefer this over ``RoutingStorage``."""
    config.validate()
    if config.storage_backend == "sqlite":
        from .storage_sqlite import SQLiteRoutingStorage

        return SQLiteRoutingStorage(config)
    return JsonlStorage(config)


def _json_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


class JsonlStorage:
    """Legacy append-only JSONL index with atomic raw artifact writes."""

    def __init__(self, config: RoutingConfig):
        self._config = config
        self.base_dir = config.base_dir.expanduser()
        self.raw_dir = self.base_dir / "raw"
        self.index_dir = self.base_dir / "index"
        self.cache_dir = self.base_dir / "cache"
        self.artifacts_jsonl = self.index_dir / "artifacts.jsonl"
        self.route_runs_jsonl = self.index_dir / "route_runs.jsonl"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write_text(self, final_path: Path, text: str) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(final_path.parent), prefix=final_path.name + ".", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(final_path)
        except OSError as exc:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise StorageWriteError(str(exc)) from exc

    def persist_raw_artifact(self, turn_id: str, kind: str, text: str) -> RawArtifact:
        now = datetime.now(UTC)
        artifact_id = f"art_{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        day_dir = self.raw_dir / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{artifact_id}_{kind}.txt"
        try:
            self._atomic_write_text(path, text)
        except StorageWriteError:
            raise
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raw = RawArtifact(
            artifact_id=artifact_id,
            turn_id=turn_id,
            kind=kind,
            created_at=now.isoformat(),
            path=str(path),
            size_bytes=len(text.encode("utf-8")),
            sha256=sha256,
            redaction_status="none",
        )
        try:
            self._append_jsonl(self.artifacts_jsonl, raw.to_dict())
        except StorageWriteError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return raw

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
        raw = self.persist_raw_artifact(turn_id=turn_id, kind=fact_type, text=raw_text)
        now = datetime.now(UTC).isoformat()
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
            created_at=now,
            updated_at=now,
            classification_source=classification_source,
            verification_status=verification_status,
        )
        try:
            self.append_envelope(env)
            if pinned:
                self.append_pin(env.memory_id, "created pinned")
        except StorageWriteError:
            try:
                Path(raw.path).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return env

    def append_envelope(self, env: MemoryEnvelope) -> None:
        self._append_jsonl(self.index_dir / "envelopes.jsonl", env.to_dict())

    def append_pin(self, memory_id: str, reason: str) -> None:
        self._append_jsonl(
            self.index_dir / "pins.jsonl",
            {
                "memory_id": memory_id,
                "reason": reason,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    def append_conflict(self, conflict: ConflictRecord) -> None:
        self._append_jsonl(self.index_dir / "conflicts.jsonl", conflict.to_dict())

    def insert_route_run(self, run: RouteRun) -> int:
        """Best-effort: tracing must not break routing."""
        try:
            payload = run.to_dict()
            payload["created_at"] = datetime.now(UTC).isoformat()
            self._append_jsonl(self.route_runs_jsonl, payload)
            return 0
        except Exception:
            return -1

    def read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise IndexCorruptionError(f"Corrupt JSONL at {path}: {exc}") from exc
        except OSError as exc:
            raise StorageReadError(str(exc)) from exc
        return rows

    def list_envelopes(self) -> list[MemoryEnvelope]:
        rows = self.read_jsonl(self.index_dir / "envelopes.jsonl")
        return [MemoryEnvelope(**row) for row in rows]

    def list_conflicts(self) -> list[ConflictRecord]:
        rows = self.read_jsonl(self.index_dir / "conflicts.jsonl")
        return [ConflictRecord(**row) for row in rows]

    def list_envelopes_and_conflicts(self) -> tuple[list[MemoryEnvelope], list[ConflictRecord]]:
        return (self.list_envelopes(), self.list_conflicts())

    def list_artifacts(self) -> list[RawArtifact]:
        rows = self.read_jsonl(self.artifacts_jsonl)
        return [RawArtifact(**row) for row in rows]

    def get_artifact(self, artifact_id: str) -> RawArtifact | None:
        for art in self.list_artifacts():
            if art.artifact_id == artifact_id:
                return art
        return None

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

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json_line(payload)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StorageWriteError(str(exc)) from exc

    def doctor(self) -> DoctorReport:
        issues: list[str] = []
        warnings: list[str] = []
        hints: list[str] = [
            "JSONL legacy mode: SQLite is the recommended production backend.",
            "Reindex from raw is best-effort only; prefer migrating to SQLite.",
        ]
        for name, path in (
            ("artifacts", self.artifacts_jsonl),
            ("envelopes", self.index_dir / "envelopes.jsonl"),
            ("conflicts", self.index_dir / "conflicts.jsonl"),
        ):
            if not path.exists():
                continue
            try:
                self.read_jsonl(path)
            except IndexCorruptionError as exc:
                issues.append(f"Corrupt JSONL ({name}): {exc}")
        arts = self.list_artifacts()
        for art in arts:
            if not Path(art.path).is_file():
                issues.append(f"Missing raw artifact file for {art.artifact_id}: {art.path}")
        if issues:
            hints.append("If envelopes reference missing raws, run `hermes-mp reindex --dry-run` (limited) or restore from backup.")
        return DoctorReport(ok=not issues, backend="jsonl", db_path=None, issues=issues, warnings=warnings, hints=hints)

    def migrate_schema(self) -> tuple[list[str], list[str]]:
        raise UnsupportedStorageOperation("migrate_schema requires SQLite backend")

    def stats(self) -> StorageStats:
        envs = self.list_envelopes()
        arts = self.list_artifacts()
        confs = self.list_conflicts()
        rooms: dict[str, int] = {}
        fts: dict[str, int] = {}
        for e in envs:
            rooms[e.room] = rooms.get(e.room, 0) + 1
            fts[e.fact_type] = fts.get(e.fact_type, 0) + 1
        red: dict[str, int] = {}
        for a in arts:
            red[a.redaction_status] = red.get(a.redaction_status, 0) + 1
        unresolved = sum(1 for c in confs if (c.status or "unresolved") == "unresolved")
        return StorageStats(
            backend="jsonl",
            envelopes=len(envs),
            artifacts=len(arts),
            conflicts=len(confs),
            unresolved_conflicts=unresolved,
            resolved_conflicts=max(0, len(confs) - unresolved),
            pinned_envelopes=sum(1 for e in envs if e.pinned),
            rooms=rooms,
            fact_types=fts,
            redaction=red,
            db_path=None,
        )

    def reindex_from_raw(self, *, dry_run: bool = True) -> ReindexResult:
        notes = [
            "JSONL reindex is best-effort: only missing artifact index rows can be partially reconciled.",
        ]
        return ReindexResult(dry_run=dry_run, backend="jsonl", notes=notes, errors=[])

    def set_memory_pinned(self, memory_id: str, pinned: bool, reason: str) -> None:
        envs = self.list_envelopes()
        if not any(e.memory_id == memory_id for e in envs):
            raise StorageWriteError(f"Unknown memory_id: {memory_id}")
        if pinned:
            self.append_pin(memory_id, reason)
            return
        raise UnsupportedStorageOperation(
            "JSONL legacy backend cannot unpin reliably (append-only index). Use SQLite."
        )

    def find_memory_by_artifact_sha256(self, sha256: str) -> MemoryEnvelope | None:
        arts = {a.artifact_id: a for a in self.list_artifacts()}
        for aid, a in arts.items():
            if a.sha256 == sha256:
                for env in self.list_envelopes():
                    if aid in env.provenance_artifact_ids:
                        return env
        return None


class RoutingStorage(JsonlStorage):
    """Backward-compatible constructor for tests and legacy callers."""

    def __init__(self, base_dir: Path | RoutingConfig):
        if isinstance(base_dir, RoutingConfig):
            cfg = base_dir
            if cfg.storage_backend != "jsonl":
                raise ValueError("RoutingStorage alias only supports JSONL; use create_storage() for SQLite")
        else:
            cfg = RoutingConfig(base_dir=base_dir, storage_backend="jsonl")
        super().__init__(cfg)
