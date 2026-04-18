from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import hashlib
import json

from .models import ConflictRecord, MemoryEnvelope, RawArtifact


class RoutingStorage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.raw_dir = base_dir / "raw"
        self.index_dir = base_dir / "index"
        self.cache_dir = base_dir / "cache"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def persist_raw_artifact(self, turn_id: str, kind: str, text: str) -> RawArtifact:
        now = datetime.now(UTC)
        artifact_id = f"art_{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        day_dir = self.raw_dir / now.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        path = day_dir / f"{artifact_id}_{kind}.txt"
        path.write_text(text, encoding="utf-8")

        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RawArtifact(
            artifact_id=artifact_id,
            turn_id=turn_id,
            kind=kind,
            created_at=now.isoformat(),
            path=str(path),
            size_bytes=len(text.encode("utf-8")),
            sha256=sha256,
        )

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

    def read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def list_envelopes(self) -> list[MemoryEnvelope]:
        rows = self.read_jsonl(self.index_dir / "envelopes.jsonl")
        return [MemoryEnvelope(**row) for row in rows]

    def list_conflicts(self) -> list[ConflictRecord]:
        rows = self.read_jsonl(self.index_dir / "conflicts.jsonl")
        return [ConflictRecord(**row) for row in rows]

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
