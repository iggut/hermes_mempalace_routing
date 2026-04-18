#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SmokeCase:
    name: str
    query: str
    expected_wing: str
    expected_room: str


CASES: list[SmokeCase] = [
    SmokeCase(
        name="android-bridge-reconnect-loop",
        query="Use MemPalace search to find the drawer about the Android Hermes Bridge reconnect loop fix. Reply with JSON only: {\"wing\":\"...\",\"room\":\"...\",\"reason\":\"...\"}.",
        expected_wing="openclaw",
        expected_room="learnings",
    ),
    SmokeCase(
        name="proactive-routing-decision",
        query="Use MemPalace search to find the drawer about the proactive routing approach decision. Reply with JSON only: {\"wing\":\"...\",\"room\":\"...\",\"reason\":\"...\"}.",
        expected_wing="openclaw",
        expected_room="decisions",
    ),
    SmokeCase(
        name="token-economics-fix",
        query="Use MemPalace search to find the drawer about the token economics fix that reduced token overhead. Reply with JSON only: {\"wing\":\"...\",\"room\":\"...\",\"reason\":\"...\"}.",
        expected_wing="wing_jupiter",
        expected_room="diary",
    ),
]


def smoke_command_prefix() -> list[str]:
    raw = os.environ.get("HERMES_SMOKE_COMMAND", "hermes")
    return shlex.split(raw)


def run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)


def extract_json_blob(output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in output:\n{output}")
    blob = output[start : end + 1]
    return json.loads(blob)


def main() -> int:
    provider = os.environ.get("HERMES_SMOKE_PROVIDER", "openai-codex")
    model = os.environ.get("HERMES_SMOKE_MODEL", "gpt-5.4")
    timeout = int(os.environ.get("HERMES_SMOKE_TIMEOUT", "600"))

    print(f"repo={ROOT}")
    print(f"provider={provider} model={model}")

    precheck = run(smoke_command_prefix() + ["mcp", "test", "mempalace"], timeout=300)
    print("\n== mempalace MCP precheck ==")
    print(precheck.stdout.rstrip())
    if precheck.returncode != 0:
        print(f"PRECHECK_FAIL exit={precheck.returncode}")
        return precheck.returncode

    failures = 0
    for case in CASES:
        print(f"\n== case: {case.name} ==")
        cmd = smoke_command_prefix() + [
            "chat",
            "-Q",
            "--provider",
            provider,
            "-m",
            model,
            "-q",
            case.query,
        ]
        proc = run(cmd, timeout=timeout)
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            print(f"CASE_FAIL {case.name}: hermes exited {proc.returncode}")
            failures += 1
            continue
        try:
            data = extract_json_blob(proc.stdout)
        except Exception as exc:
            print(f"CASE_FAIL {case.name}: could not parse JSON: {exc}")
            failures += 1
            continue
        wing = str(data.get("wing", "")).strip().lower()
        room = str(data.get("room", "")).strip().lower()
        reason = str(data.get("reason", "")).strip()
        ok = wing == case.expected_wing and room == case.expected_room
        print(f"parsed wing={wing!r} room={room!r}")
        print(f"expected wing={case.expected_wing!r} room={case.expected_room!r}")
        print(f"reason={reason!r}")
        if not ok:
            print(f"CASE_FAIL {case.name}: exact match mismatch")
            failures += 1
        else:
            print(f"CASE_OK {case.name}")

    if failures:
        print(f"\nSMOKE_FAIL failures={failures}")
        return 1
    print("\nSMOKE_OK all cases matched expected drawers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
