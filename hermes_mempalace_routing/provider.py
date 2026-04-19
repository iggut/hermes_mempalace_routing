from __future__ import annotations

import hashlib
import re

from .config import RoutingConfig
from .conflicts import detect_conflicts
from .models import ArtifactKind, ClassificationSource, MemoryEnvelope
from .redaction import redact_text
from .storage import StorageBackend, StorageWriteError


class ArtifactValidationError(ValueError):
    """Invalid ingest payload (safe to surface when not fail-open)."""


_STACK_HINT = re.compile(r"(?i)(traceback|exception in thread|^\s*File \")")
_SHELL_HINT = re.compile(r"(?i)(command failed|exit code|errno|segmentation fault)")


def _normalize_for_signature(text: str, enabled: bool) -> str:
    t = text.strip()
    if not enabled:
        return t
    t = re.sub(r"\s+", " ", t)
    return t


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _classify_fact_type(raw_text: str) -> str:
    """Deterministic classification for stderr-like / stack-like content."""
    if _STACK_HINT.search(raw_text):
        return ArtifactKind.STACKTRACE.value
    if _SHELL_HINT.search(raw_text) or raw_text.strip().startswith("$"):
        return ArtifactKind.SHELL_OUTPUT.value
    if "tool" in raw_text.lower()[:120] and "error" in raw_text.lower()[:400]:
        return ArtifactKind.TOOL_OUTPUT.value
    return ArtifactKind.NOTE.value


def validate_ingest(
    *,
    room: str,
    fact_type: str,
    summary: str,
    raw_text: str,
    kind_hint: str | None = None,
) -> tuple[str, str]:
    """
    Validate and normalize artifact kind / fact type.

    Returns (artifact_kind, fact_type) for persistence.
    """
    if not room or not str(room).strip():
        raise ArtifactValidationError("room is required")
    if summary is None:
        raise ArtifactValidationError("summary is required")
    ft = (kind_hint or fact_type or "").strip().lower() or ArtifactKind.OTHER.value
    allowed = {m.value for m in ArtifactKind}
    if ft not in allowed:
        ft = _classify_fact_type(raw_text)
    elif ft == ArtifactKind.OTHER.value:
        ft = _classify_fact_type(raw_text)
    return ft, ft


class MemPalaceRoutingProvider:
    def __init__(self, storage: StorageBackend, config: RoutingConfig):
        self.storage = storage
        self._config = config

    def _sync_conflicts(self) -> None:
        envelopes = self.storage.list_envelopes()
        stored = self.storage.list_conflicts()
        merged = detect_conflicts(envelopes, self._config, stored=stored)
        for c in merged:
            self.storage.append_conflict(c)

    def refresh_conflicts(self) -> None:
        """Recompute conflict records after envelope changes (e.g. MemPalace-backed rows)."""
        self._sync_conflicts()

    def store_artifact_as_memory(
        self,
        turn_id: str,
        room: str,
        fact_type: str,
        summary: str,
        raw_text: str,
        route_tags: list[str] | None = None,
        conflict_key: str | None = None,
        pinned: bool = False,
        *,
        classification_source: str = ClassificationSource.RULE.value,
        fail_open: bool = True,
    ) -> MemoryEnvelope | None:
        """
        Persist raw artifact + envelope via deterministic ingest pipeline.

        On validation failure: raises ArtifactValidationError, unless fail_open is True
        (caller may swallow and skip persistence).
        """
        cfg = self._config
        try:
            _kind, ft = validate_ingest(
                room=room,
                fact_type=fact_type,
                summary=summary,
                raw_text=raw_text,
                kind_hint=fact_type,
            )
        except ArtifactValidationError:
            if fail_open:
                return None
            raise

        text = raw_text
        red_status = "none"
        if cfg.redact_before_persist:
            rr = redact_text(text, cfg.redaction_policy)
            text = rr.text
            if rr.status == "dropped":
                red_status = "dropped"
            elif rr.status == "masked":
                red_status = "masked"
            else:
                red_status = "none"

        if red_status == "dropped":
            if fail_open:
                return None
            raise ArtifactValidationError("artifact dropped by redaction policy")

        sha = _content_sha256(text)
        if cfg.dedupe_identical_raw:
            existing = self.storage.find_memory_by_artifact_sha256(sha)
            if existing is not None:
                return existing

        if ft in {ArtifactKind.STACKTRACE.value, ArtifactKind.SHELL_OUTPUT.value, ArtifactKind.TOOL_OUTPUT.value}:
            sig = _content_sha256(_normalize_for_signature(text, cfg.dedupe_normalize_for_signature))
            recent = sorted(
                self.storage.list_envelopes(),
                key=lambda e: e.created_at or "",
                reverse=True,
            )[: cfg.repeat_error_group_window]
            for env in recent:
                if env.room != room or env.fact_type != ft:
                    continue
                if not env.provenance_artifact_ids:
                    continue
                prev_txt = self.storage.read_artifact_text(env.provenance_artifact_ids[0])
                if prev_txt is None:
                    continue
                prev_sig = _content_sha256(_normalize_for_signature(prev_txt, cfg.dedupe_normalize_for_signature))
                if prev_sig == sig:
                    return env

        env = self.storage.persist_memory_turn(
            turn_id=turn_id,
            room=room,
            fact_type=ft,
            summary=summary,
            raw_text=text,
            route_tags=route_tags,
            conflict_key=conflict_key,
            pinned=pinned,
            classification_source=classification_source,
            verification_status="unverified",
            raw_redaction_status=red_status,
        )
        try:
            self._sync_conflicts()
        except StorageWriteError:
            raise
        return env
