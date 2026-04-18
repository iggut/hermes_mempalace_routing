from __future__ import annotations

import argparse
from pathlib import Path

from .config import RoutingConfig
from .conflicts import ConflictResolver
from .plugin import HermesMemPalaceRoutingPlugin


def _plugin_from_args(args) -> HermesMemPalaceRoutingPlugin:
    base_dir = Path(args.base_dir).expanduser() if getattr(args, "base_dir", None) else RoutingConfig.default().base_dir
    return HermesMemPalaceRoutingPlugin(RoutingConfig(base_dir=base_dir))


def cmd_route(args) -> int:
    plugin = _plugin_from_args(args)
    payload = plugin.build_context_for_query(
        query=args.query,
        total_tokens=args.total_tokens,
        active_project=args.active_project,
        mode=args.mode,
    )
    print(f"Active project: {args.active_project or 'none'}")
    print(f"Mode: {args.mode}")
    print(f"Budget: {payload['budget']}")
    print("Route ranking (score, rationale):")
    for cand in payload["route_candidates"][:15]:
        print(f"  {cand.memory_id} score={cand.score:.3f} room={cand.room} rationale={cand.rationale}")
    print(payload["rendered_block"])
    return 0


def cmd_inspect(args) -> int:
    plugin = _plugin_from_args(args)
    envelopes = plugin.storage.list_envelopes()

    if args.target_type == "room":
        matches = [env for env in envelopes if env.room == args.target_id]
        print(f"Room: {args.target_id}")
        for env in matches:
            print(f"- {env.memory_id}: {env.summary}")
        return 0

    if args.target_type == "memory":
        for env in envelopes:
            if env.memory_id == args.target_id:
                print(env)
                return 0
        print("memory not found")
        return 1

    # artifact: prefer artifacts index (source of truth), then envelope provenance.
    art = plugin.storage.get_artifact(args.target_id)
    if art is not None:
        print(art)
        body = plugin.storage.read_artifact_text(args.target_id)
        if body is not None:
            print("--- raw (exact, utf-8) ---")
            print(body)
        return 0
    for env in envelopes:
        if args.target_id in env.provenance_artifact_ids:
            print(env)
            return 0
    print("artifact not found")
    return 1


def cmd_pin(args) -> int:
    plugin = _plugin_from_args(args)
    plugin.storage.append_pin(args.memory_id, args.reason)
    print(f"Pinned {args.memory_id}: {args.reason}")
    return 0


def cmd_conflicts(args) -> int:
    plugin = _plugin_from_args(args)
    envelopes = plugin.storage.list_envelopes()
    if args.room:
        envelopes = [env for env in envelopes if env.room == args.room]
    conflicts = ConflictResolver().detect(envelopes)
    if not conflicts:
        print("No conflicts detected")
        return 0
    for conflict in conflicts:
        print(conflict)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-mp")
    parser.add_argument("--base-dir", default=str(RoutingConfig.default().base_dir))
    sub = parser.add_subparsers(dest="command", required=True)

    p_route = sub.add_parser("route")
    p_route.add_argument("query")
    p_route.add_argument("--active-project")
    p_route.add_argument("--mode", choices=["debugging", "design"], default="debugging")
    p_route.add_argument("--total-tokens", type=int, default=12000)
    p_route.set_defaults(func=cmd_route)

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("target_type", choices=["room", "memory", "artifact"])
    p_inspect.add_argument("target_id")
    p_inspect.set_defaults(func=cmd_inspect)

    p_pin = sub.add_parser("pin")
    p_pin.add_argument("memory_id")
    p_pin.add_argument("--reason", default="operator pin")
    p_pin.set_defaults(func=cmd_pin)

    p_conflicts = sub.add_parser("conflicts")
    p_conflicts.add_argument("--room")
    p_conflicts.set_defaults(func=cmd_conflicts)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
