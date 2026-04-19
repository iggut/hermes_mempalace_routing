from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import project_room

# Route tags for MemPalace-backed rows (routing metadata / search hydration).
MEMPALACE_VERBATIM_TAG = "mempalace_verbatim"
MEMPALACE_DURABLE_TAG = "mempalace_durable"


class ScopeSanitizeError(ValueError):
    """Wing/room/content failed MemPalace-style ingress validation."""


_WING_ROOM_BAD = re.compile(r"[^\w\-./]+", re.UNICODE)
_COLLAPSE = re.compile(r"\s+")


def sanitize_wing_room(name: str, *, max_len: int = 128) -> str:
    """Normalize a wing or room segment for filing (MemPalace-style ingress)."""
    t = name.strip()
    if not t:
        raise ScopeSanitizeError("wing/room name is empty")
    t = t.lower()
    t = _COLLAPSE.sub("-", t.replace("/", "-slash-"))
    t = _WING_ROOM_BAD.sub("", t)
    t = t.strip("-._") or "unknown"
    if len(t) > max_len:
        t = t[:max_len].rstrip("-")
    return t or "unknown"


def sanitize_drawer_content(text: str, *, max_chars: int = 1_000_000) -> str:
    """Strip and cap verbatim drawer body; does not summarize or rewrite meaning."""
    if text is None:
        raise ScopeSanitizeError("drawer content is None")
    s = str(text)
    if not s.strip():
        raise ScopeSanitizeError("drawer content is empty")
    if len(s) > max_chars:
        raise ScopeSanitizeError(f"drawer content exceeds max_chars={max_chars}")
    return s


WingRoomStrategy = Literal["active_project", "fixed"]
DefaultRoomStrategy = Literal["fact_type_and_project", "project_only", "fixed_decisions"]


@dataclass(slots=True)
class MemPalaceScope:
    """Explicit Hermes → MemPalace wing/room mapping (interoperability vocabulary)."""

    wing: str
    room: str


def derive_mempalace_scope(
    *,
    active_project: str | None,
    fact_type: str,
    room_hint: str | None = None,
    wing_strategy: WingRoomStrategy = "active_project",
    room_strategy: DefaultRoomStrategy = "fact_type_and_project",
    fixed_wing: str | None = None,
    fixed_room: str | None = None,
) -> MemPalaceScope:
    """
    Deterministic mapping: active project → wing; fact type + project → room.

    - **wing** (MemPalace): sanitized `active_project`, or `fixed_wing`, or `global`.
    - **room** (MemPalace): optional explicit `room_hint` (``project/...`` or standard room);
      otherwise diagnostics → ``errors``, else ``project/<wing>`` for durable notes.
    """
    if wing_strategy == "fixed":
        w = sanitize_wing_room(fixed_wing or "global")
    else:
        w = sanitize_wing_room(active_project or "global")

    if room_strategy == "fixed_decisions" and fixed_room:
        return MemPalaceScope(wing=w, room=sanitize_wing_room(fixed_room))

    if room_hint and str(room_hint).strip():
        rh = room_hint.strip()
        if rh.lower().startswith("project/"):
            seg = rh.split("/", 1)[1]
            return MemPalaceScope(wing=w, room=project_room(seg))
        return MemPalaceScope(wing=w, room=sanitize_wing_room(rh))

    ft = (fact_type or "note").strip().lower()
    if ft in {"stacktrace", "shell_output", "tool_output"}:
        return MemPalaceScope(wing=w, room="errors")

    if room_strategy == "project_only":
        return MemPalaceScope(wing=w, room=project_room(w))

    return MemPalaceScope(wing=w, room=project_room(w))
