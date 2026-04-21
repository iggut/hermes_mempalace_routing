from __future__ import annotations

from pathlib import Path

from hermes_mempalace_routing.config import RoutingConfig
from hermes_mempalace_routing.host_hooks import HermesHostHooks
from hermes_mempalace_routing.mempalace_adapter import MemPalaceAdapter
from hermes_mempalace_routing.plugin import HermesMemPalaceRoutingPlugin


def _mp_config(tmp: Path) -> RoutingConfig:
    return RoutingConfig(
        base_dir=tmp,
        storage_backend="sqlite",
        memory_backend="mempalace_first",
        mempalace_enabled=True,
        write_raw_artifacts=False,
        redact_before_persist=True,
        redaction_policy="none",
        replace_hermes_summarization=True,
        enabled=True,
    )


def test_mempalace_durable_write_adds_drawer_and_skips_duplicate(tmp_path: Path) -> None:
    state = {"dup": False, "adds": 0}

    def mempalace_check_duplicate(content: str, wing: str, room: str, threshold: float = 0.92):
        if state["dup"]:
            return {"duplicate": True, "drawer_id": "d1"}
        return {"duplicate": False}

    def mempalace_add_drawer(content: str, wing: str, room: str, metadata: dict | None = None):
        state["adds"] += 1
        return "drawer-new-1"

    def mempalace_search(query: str, wing: str | None = None, room: str | None = None, limit: int = 24):
        return {"results": []}

    plugin = HermesMemPalaceRoutingPlugin(
        _mp_config(tmp_path),
        mempalace_tools={
            "mempalace_status": lambda: {"ok": True},
            "mempalace_check_duplicate": mempalace_check_duplicate,
            "mempalace_add_drawer": mempalace_add_drawer,
            "mempalace_search": mempalace_search,
        },
    )

    e1 = plugin.record_turn_artifact(
        turn_id="t1",
        room="project/hermes",
        fact_type="note",
        summary="decision",
        raw_text="use sqlite for metadata",
        active_project="hermes",
    )
    assert e1 is not None
    assert e1.memory_id == "mem_mp_drawer-new-1"
    assert state["adds"] == 1

    state["dup"] = True
    e2 = plugin.record_turn_artifact(
        turn_id="t2",
        room="project/hermes",
        fact_type="note",
        summary="decision",
        raw_text="use sqlite for metadata",
        active_project="hermes",
    )
    assert e2 is None
    assert state["adds"] == 1


def test_mempalace_search_hydrates_routing_and_fail_open_on_search_error(tmp_path: Path) -> None:
    def mempalace_search(query: str, wing: str | None = None, room: str | None = None, limit: int = 24):
        if "boom" in query:
            raise RuntimeError("network down")

        return {
            "results": [
                {
                    "drawer_id": "z9",
                    "wing": "hermes",
                    "room": "project/hermes",
                    "content": "pinned port 7777",
                }
            ]
        }

    def mempalace_add_drawer(content: str, wing: str, room: str, metadata: dict | None = None):
        return "x"

    cfg = _mp_config(tmp_path)
    plugin = HermesMemPalaceRoutingPlugin(
        cfg,
        mempalace_tools={
            "mempalace_status": lambda: {"ok": True},
            "mempalace_check_duplicate": lambda *a, **k: {"duplicate": False},
            "mempalace_add_drawer": mempalace_add_drawer,
            "mempalace_search": mempalace_search,
        },
    )

    payload = plugin.build_context_for_query(
        query="what port",
        total_tokens=8000,
        active_project="hermes",
        mode="debugging",
    )
    assert "7777" in payload["rendered_block"]
    assert payload["fallback_used"] is False

    payload2 = plugin.build_context_for_query(
        query="boom",
        total_tokens=8000,
        active_project="hermes",
        mode="debugging",
    )
    assert payload2["fallback_used"] is False
    assert payload2["error"] is None


def test_session_wake_populates_resume_cache(tmp_path: Path) -> None:
    def mempalace_search(query: str, wing: str | None = None, room: str | None = None, limit: int = 24):
        return {"results": []}

    def mempalace_resume(query: str, wing: str | None = None, room: str | None = None, limit: int = 16):
        return {
            "results": [
                {
                    "drawer_id": "r1",
                    "wing": "hermes",
                    "room": "project/hermes",
                    "content": "resume fact",
                }
            ]
        }

    cfg = _mp_config(tmp_path)
    plugin = HermesMemPalaceRoutingPlugin(
        cfg,
        mempalace_tools={
            "mempalace_status": lambda: {"ok": True},
            "mempalace_check_duplicate": lambda *a, **k: {"duplicate": False},
            "mempalace_add_drawer": lambda *a, **k: "noop",
            "mempalace_search": mempalace_search,
            "mempalace_resume": mempalace_resume,
        },
    )
    hooks = HermesHostHooks(plugin)
    out = hooks.session_wake_or_resume("ctx", active_project="hermes")
    assert out["resume_error"] is None
    assert len(out["resume_envelopes"]) == 1

    payload = hooks.pre_model_context_assembly(
        query="resume",
        total_tokens=8000,
        active_project="hermes",
        mode="debugging",
    )
    assert "resume fact" in payload["rendered_block"]


def test_mempalace_first_filters_legacy_local_envelopes(tmp_path: Path) -> None:
    cfg = RoutingConfig(
        base_dir=tmp_path,
        storage_backend="sqlite",
        memory_backend="mempalace_first",
        mempalace_enabled=False,
        mempalace_include_legacy_local_envelopes=False,
        replace_hermes_summarization=True,
        enabled=True,
    )
    plugin = HermesMemPalaceRoutingPlugin(cfg)
    store = plugin.storage
    env = store.persist_memory_turn(
        turn_id="t",
        room="decisions",
        fact_type="note",
        summary="legacy",
        raw_text="old local durable",
        route_tags=[],
        conflict_key=None,
        pinned=False,
    )
    assert env.memory_id.startswith("mem_")

    payload = plugin.build_context_for_query(
        query="legacy",
        total_tokens=8000,
        active_project=None,
        mode="debugging",
    )
    assert "old local durable" not in payload["rendered_block"]


def test_derive_mempalace_scope_project_room():
    from hermes_mempalace_routing.mempalace_scope import derive_mempalace_scope

    s = derive_mempalace_scope(
        active_project="hermes",
        fact_type="note",
        room_hint="project/hermes",
    )
    assert s.wing == "hermes"
    assert s.room == "project/hermes"


def test_mempalace_search_normalizes_items_and_nested_drawer() -> None:
    def mempalace_search(query: str, wing: str | None = None, room: str | None = None, limit: int = 24):
        return {
            "items": [
                {
                    "drawer": {"id": "nested-1", "body": "verbatim body"},
                    "wing": "w",
                    "room": "project/w",
                }
            ]
        }

    adapter = MemPalaceAdapter({"mempalace_search": mempalace_search})
    hits = adapter.search("q", wing="w", limit=8)
    assert len(hits) == 1
    assert hits[0].drawer_id == "nested-1"
    assert hits[0].content == "verbatim body"
