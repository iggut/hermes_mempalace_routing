from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


class MemPalaceAdapterError(Exception):
    """Typed failure from a MemPalace boundary call (does not imply chat should abort)."""


@dataclass(slots=True)
class MemPalaceDrawerHit:
    """One search/resume result: drawer identity + wing/room + verbatim body."""

    drawer_id: str
    wing: str
    room: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _as_str(d: object, key: str, default: str = "") -> str:
    if not isinstance(d, dict):
        return default
    v = d.get(key)
    return default if v is None else str(v)


def _normalize_status(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "raw": payload, "error": "invalid_status_payload"}
    ok = bool(payload.get("ok", payload.get("healthy", True)))
    return {
        "ok": ok,
        "version": _as_str(payload, "version"),
        "detail": payload.get("detail") or payload.get("message"),
        "raw": payload,
    }


def _flatten_drawer_item(it: dict[str, Any]) -> dict[str, Any]:
    nested = it.get("drawer")
    if isinstance(nested, dict):
        merged = {**nested, **it}
        merged.pop("drawer", None)
        return merged
    return it


def _normalize_search(payload: object) -> list[MemPalaceDrawerHit]:
    if payload is None:
        return []
    items: list[Any]
    if isinstance(payload, dict):
        items = (
            payload.get("results")
            or payload.get("hits")
            or payload.get("drawers")
            or payload.get("items")
            or payload.get("data")
            or []
        )
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out: list[MemPalaceDrawerHit] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        it = _flatten_drawer_item(raw)
        did = _as_str(it, "drawer_id") or _as_str(it, "id") or _as_str(it, "drawerId")
        wing = _as_str(it, "wing")
        room = _as_str(it, "room")
        content = (
            _as_str(it, "content")
            or _as_str(it, "verbatim")
            or _as_str(it, "text")
            or _as_str(it, "body")
        )
        if not content:
            continue
        if not did:
            key_src = f"{wing}|{room}|{content[:512]}"
            hkey = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24]
            did = f"mp_hit_{hkey}"
        skip = {
            "drawer_id",
            "id",
            "drawerId",
            "wing",
            "room",
            "content",
            "verbatim",
            "text",
            "body",
        }
        meta = {k: v for k, v in it.items() if k not in skip}
        out.append(MemPalaceDrawerHit(drawer_id=did, wing=wing, room=room, content=content, metadata=meta))
    return out


def _normalize_duplicate(payload: object) -> tuple[bool, str | None]:
    if not isinstance(payload, dict):
        return False, None
    if payload.get("error"):
        return False, None
    dup = payload.get("duplicate")
    if dup is None:
        dup = payload.get("is_duplicate")
    if dup is None:
        dup = payload.get("match")
    is_dup = bool(dup) if dup is not None else False
    mid = (
        payload.get("match_id")
        or payload.get("best_match_id")
        or payload.get("drawer_id")
        or payload.get("matched_drawer_id")
    )
    if mid is None and isinstance(payload.get("matches"), list) and payload["matches"]:
        first = payload["matches"][0]
        if isinstance(first, dict):
            mid = first.get("id") or first.get("drawer_id")
    return is_dup, (str(mid) if mid else None)


def _normalize_add_drawer(payload: object) -> str:
    if isinstance(payload, dict):
        if payload.get("error") and not payload.get("drawer_id"):
            raise MemPalaceAdapterError(str(payload.get("error")))
        if payload.get("success") is False and not payload.get("drawer_id"):
            raise MemPalaceAdapterError(str(payload.get("error") or "add_drawer failed"))
        did = payload.get("drawer_id") or payload.get("id")
        if did:
            return str(did)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    raise MemPalaceAdapterError("add_drawer response missing drawer id")


def _uses_hermes_mcp_args_style(fn: Callable[..., Any]) -> bool:
    """Hermes ``tools.mcp_tool._make_tool_handler`` uses ``(args: dict, **kwargs) -> str``."""
    try:
        sig = inspect.signature(fn)
        names = list(sig.parameters.keys())
        return bool(names) and names[0] == "args"
    except (TypeError, ValueError):
        return False


def _build_mcp_tool_arguments(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """JSON ``arguments`` for MemPalace MCP tools (matches upstream ``mcp_server.py`` schemas)."""
    if name in ("mempalace_status", "mempalace_reconnect", "mempalace_get_taxonomy", "mempalace_list_wings"):
        return {}
    if name == "mempalace_list_rooms":
        w = kwargs.get("wing")
        return {} if w is None else {"wing": w}
    if name in ("mempalace_search", "mempalace_resume"):
        q = args[0] if args else ""
        out: dict[str, Any] = {
            "query": str(q),
            "limit": int(kwargs.get("limit", 24)),
        }
        if kwargs.get("wing") is not None:
            out["wing"] = kwargs["wing"]
        if kwargs.get("room") is not None:
            out["room"] = kwargs["room"]
        return out
    if name == "mempalace_check_duplicate":
        content = args[0] if args else ""
        return {"content": str(content), "threshold": float(kwargs.get("threshold", 0.92))}
    if name == "mempalace_add_drawer":
        content = args[0] if args else ""
        meta = kwargs.get("metadata") or {}
        spec: dict[str, Any] = {
            "wing": str(kwargs.get("wing") or ""),
            "room": str(kwargs.get("room") or ""),
            "content": str(content),
            "added_by": "hermes_mempalace_routing",
        }
        if meta:
            try:
                spec["source_file"] = json.dumps(meta, ensure_ascii=False)[:2000]
            except Exception:
                pass
        return spec
    if name == "mempalace_delete_drawer":
        did = args[0] if args else ""
        return {"drawer_id": str(did)}
    raise ValueError(f"no MCP argument mapping for tool {name!r}")


def _unwrap_hermes_mcp_json_result(raw: Any) -> Any:
    """Parse JSON strings from Hermes MCP wrappers; unwrap ``result`` / ``structuredContent``."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(parsed, dict):
        return parsed
    if "structuredContent" in parsed and isinstance(parsed["structuredContent"], dict):
        return parsed["structuredContent"]
    if "result" in parsed:
        inner = parsed["result"]
        if isinstance(inner, str):
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                return inner
        return inner
    return parsed


def _coerce_tool_return(raw: Any) -> Any:
    """Hermes MCP handlers return JSON strings; tests often return dicts."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return _unwrap_hermes_mcp_json_result(raw)
            except json.JSONDecodeError:
                return raw
    return raw


class MemPalaceAdapter:
    """Thin wrapper over the MemPalace MCP/tool surface (callables injected by the host)."""

    def __init__(self, tools: Mapping[str, Callable[..., Any]] | None = None) -> None:
        self._tools = dict(tools or {})

    def bind(self, **tools: Callable[..., Any]) -> None:
        self._tools.update(tools)

    def tooling_ready(self) -> bool:
        """True when minimal callables for durable write + recall are present."""
        return (
            self._tools.get("mempalace_add_drawer") is not None
            and self._tools.get("mempalace_search") is not None
        )

    def _invoke_bound(
        self,
        fn: Callable[..., Any],
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Call host-injected tool: Hermes MCP ``(args: dict)->str`` or legacy ``(*args, **kwargs)``."""
        if _uses_hermes_mcp_args_style(fn):
            try:
                payload = _build_mcp_tool_arguments(name, args, kwargs)
            except ValueError:
                raw = fn(*args, **kwargs)
                return _coerce_tool_return(raw)
            raw = fn(payload)
            return _coerce_tool_return(raw)
        raw = fn(*args, **kwargs)
        return _coerce_tool_return(raw)

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = self._tools.get(name)
        if fn is None:
            return None
        try:
            return self._invoke_bound(fn, name, args, kwargs)
        except Exception as exc:
            raise MemPalaceAdapterError(f"{name} failed: {exc}") from exc

    def status(self) -> dict[str, Any]:
        """Maps to ``mempalace_status``."""
        raw = self._call("mempalace_status")
        if raw is None:
            return {"ok": False, "error": "mempalace_status_not_configured", "raw": None}
        return _normalize_status(raw)

    def reconnect(self) -> dict[str, Any]:
        """Maps to ``mempalace_reconnect``."""
        raw = self._call("mempalace_reconnect")
        if raw is None:
            return {"ok": True, "detail": "reconnect_not_configured_noop", "raw": None}
        if isinstance(raw, dict):
            return _normalize_status(raw)
        return {"ok": True, "raw": raw}

    def list_wings(self) -> list[str]:
        raw = self._call("mempalace_list_wings")
        if raw is None:
            return []
        if isinstance(raw, dict) and "wings" in raw:
            w = raw["wings"]
            return [str(x) for x in w] if isinstance(w, list) else []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return []

    def list_rooms(self, *, wing: str | None = None) -> list[str]:
        raw = self._call("mempalace_list_rooms", wing=wing)
        if raw is None:
            return []
        if isinstance(raw, dict) and "rooms" in raw:
            r = raw["rooms"]
            return [str(x) for x in r] if isinstance(r, list) else []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return []

    def get_taxonomy(self) -> dict[str, Any]:
        raw = self._call("mempalace_get_taxonomy")
        return raw if isinstance(raw, dict) else {}

    def search(
        self,
        query: str,
        *,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 24,
    ) -> list[MemPalaceDrawerHit]:
        """Maps to ``mempalace_search``."""
        raw = self._call("mempalace_search", query, wing=wing, room=room, limit=limit)
        if raw is None:
            return []
        return _normalize_search(raw)

    def resume(
        self,
        query: str,
        *,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 16,
    ) -> list[MemPalaceDrawerHit]:
        """Session wake / scoped resume; uses search with the same tool unless overridden."""
        tool_name = "mempalace_resume" if self._tools.get("mempalace_resume") else "mempalace_search"
        fn = self._tools.get(tool_name)
        if fn is None:
            return []
        try:
            raw = self._invoke_bound(fn, tool_name, (query,), dict(wing=wing, room=room, limit=limit))
            return _normalize_search(raw)
        except Exception as exc:
            raise MemPalaceAdapterError(f"resume failed: {exc}") from exc

    def check_duplicate(
        self,
        content: str,
        wing: str,
        room: str,
        *,
        threshold: float = 0.92,
    ) -> tuple[bool, str | None]:
        """Maps to ``mempalace_check_duplicate``."""
        raw = self._call(
            "mempalace_check_duplicate",
            content,
            wing=wing,
            room=room,
            threshold=threshold,
        )
        if raw is None:
            return False, None
        return _normalize_duplicate(raw)

    def add_drawer(
        self,
        content: str,
        wing: str,
        room: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Maps to ``mempalace_add_drawer``; returns new drawer id."""
        raw = self._call(
            "mempalace_add_drawer",
            content,
            wing=wing,
            room=room,
            metadata=metadata or {},
        )
        try:
            return _normalize_add_drawer(raw)
        except MemPalaceAdapterError:
            raise
        except Exception as exc:
            raise MemPalaceAdapterError(f"add_drawer parse failed: {exc}") from exc

    def delete_drawer(self, drawer_id: str) -> bool:
        """Maps to ``mempalace_delete_drawer``."""
        raw = self._call("mempalace_delete_drawer", drawer_id)
        if raw is None:
            return False
        if isinstance(raw, dict):
            if "success" in raw:
                return bool(raw.get("success"))
            return bool(raw.get("ok", True))
        return bool(raw)


def drawer_hits_to_memory_envelopes(
    hits: list[MemPalaceDrawerHit],
    *,
    fact_type_default: str = "note",
) -> list[Any]:
    """Convert search hits into :class:`MemoryEnvelope` rows for the routing engine."""
    from datetime import UTC, datetime

    from .models import ClassificationSource, MemoryEnvelope, VerificationStatus

    now = datetime.now(UTC).isoformat()
    out: list[MemoryEnvelope] = []
    for h in hits:
        mem_id = f"mem_mp_{h.drawer_id}"
        tags = ["mempalace_verbatim", "mempalace_search_hit"]
        env = MemoryEnvelope(
            memory_id=mem_id,
            room=h.room or project_room_fallback(h.wing),
            route_tags=tags,
            fact_type=fact_type_default,
            summary=f"[MemPalace drawer {h.drawer_id}]",
            provenance_artifact_ids=[],
            provenance_excerpt=h.content,
            confidence=0.75,
            pinned=False,
            conflict_key=None,
            created_at=now,
            updated_at=now,
            classification_source=ClassificationSource.IMPORT.value,
            verification_status=VerificationStatus.UNVERIFIED.value,
        )
        out.append(env)
    return out


def project_room_fallback(wing: str) -> str:
    from .config import project_room

    return project_room(wing) if wing else "scratch"
