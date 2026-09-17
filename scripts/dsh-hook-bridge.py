#!/usr/bin/env python3
"""dsh hooks bridge for the multi-window-m-036 skill.

DeepSeek Harness has no hook dialect of its own. It ships two bridges that run
*other* tools' `hooks.json` command hooks during agent runs, and the Claude Code
bridge is the only one that exposes `SubagentStop` — the event that means "a
delegated worker ended", which is exactly the second leg of this skill's window
protocol. So a dsh window wires its stop evidence through that bridge, and this
script turns one bridge payload into one ledger line via `taskctl.py hook-audit`.

Contract with the caller (one command per hook event, payload on stdin):

    py -3 dsh-hook-bridge.py                 # complete start/end pair (default)
    py -3 dsh-hook-bridge.py --record        # single observation entry, no pair
    py -3 dsh-hook-bridge.py --status <f>    # also write a last-event record

A dsh hook fires ONCE per event, unlike a Cursor stop that has a separate end
invocation. The default therefore mints the whole start/end pair in one call,
with the host `session_id` as `run_id` — that is the complete pair
`hook_supervision` requires, and it is what lets M1 tie a stop to the window
that stopped. `--record` is for low-value events that should be visible in the
ledger without claiming a pair. Nothing here mutates task status: this script
only records host evidence; `taskctl.py` still owns every verdict.

**It always exits 0.** The dsh `Stop` hook is a blocking-capable serial
listener whose `stop_hook_active` is always false and whose consecutive-block
cap is not implemented, so a non-zero exit that the bridge reads as a blocking
decision would force another model turn on every step — a self-inflicted loop.
Failures are reported on stdout/stderr and must never become an exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Bridge event name -> (taskctl --source, --intent). A dsh hook fires once per
# event, so the default `pair` intent writes a complete start/end pair in one
# call; `record` writes a single observation entry for low-value events.
EVENT_SOURCES = {
    "SessionStart": ("dsh-session-start", "record"),
    "UserPromptSubmit": ("dsh-prompt", "record"),
    "PreToolUse": ("dsh-pre-tool", "record"),
    "PostToolUse": ("dsh-post-tool", "record"),
    "Stop": ("dsh-stop", "pair"),
    "SubagentStop": ("dsh-subagent-end", "pair"),
}
DEFAULT_SOURCE = "dsh-stop"
TASKCTL_ENV = "MULTI_WINDOW_TASKCTL"
SKILL_ROOT_ENV = "MULTI_WINDOW_SKILL_ROOT"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def warn(message: str) -> None:
    sys.stderr.write(f"dsh-hook-bridge: {message}\n")


def read_payload() -> Dict[str, Any]:
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception as exc:  # pragma: no cover - stdin is a pipe in every real host
        warn(f"could not read stdin: {exc}")
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"stdin was not JSON ({exc}); recording the event with no session id")
        return {}
    if not isinstance(parsed, dict):
        warn("stdin JSON was not an object; ignoring it")
        return {}
    return parsed


def resolve_taskctl(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    from_env = os.environ.get(TASKCTL_ENV, "").strip()
    if from_env:
        candidate = Path(from_env).expanduser()
        if candidate.is_file():
            return candidate
    beside = Path(__file__).resolve().parent / "taskctl.py"
    if beside.is_file():
        return beside
    skill_root = os.environ.get(SKILL_ROOT_ENV, "").strip()
    if skill_root:
        candidate = Path(skill_root).expanduser() / "scripts" / "taskctl.py"
        if candidate.is_file():
            return candidate
    return None


def resolve_root(payload: Dict[str, Any], explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    for key in ("cwd", "project_dir", "workspace"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    return Path.cwd().resolve()


def first_string(payload: Dict[str, Any], keys: tuple) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def write_status(path: Optional[str], record: Dict[str, Any]) -> None:
    if not path:
        return
    try:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:  # diagnostics must never affect the run
        warn(f"could not write status file {path}: {exc}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="turn one dsh hooks-bridge payload into one taskctl ledger line"
    )
    parser.add_argument("--taskctl", help="explicit path to taskctl.py")
    parser.add_argument("--root", help="explicit project root; defaults to the payload cwd")
    parser.add_argument(
        "--record",
        action="store_true",
        help="write a single observation entry instead of the default start/end pair",
    )
    parser.add_argument(
        "--status",
        help="also write a small JSON record of this invocation (diagnostics only)",
    )
    parser.add_argument(
        "--event",
        help="override the payload hook_event_name (for manual dry runs)",
    )
    args = parser.parse_args(argv)

    payload = read_payload()
    event = args.event or first_string(payload, ("hook_event_name", "hookEventName", "event")) or "Stop"
    source, intent = EVENT_SOURCES.get(event, (DEFAULT_SOURCE, "pair"))
    if args.record:
        intent = "record"
    session_id = first_string(payload, ("session_id", "sessionId", "turn_id"))
    agent_id = first_string(payload, ("agent_id", "agentId", "run_id"))
    root = resolve_root(payload, args.root)

    record: Dict[str, Any] = {
        "at": utc_now(),
        "event": event,
        "source": source,
        "phase": "record" if intent == "record" else "pair",
        "session_id": session_id,
        "agent_id": agent_id,
        "project_root": str(root),
        "taskctl": "",
        "result": "",
    }

    taskctl = resolve_taskctl(args.taskctl)
    if taskctl is None:
        record["result"] = "no-taskctl"
        write_status(args.status, record)
        warn(f"taskctl.py not found; set {TASKCTL_ENV} or --taskctl")
        print(f"DSH_HOOK_SKIP no-taskctl source={source}")
        return 0

    record["taskctl"] = str(taskctl)
    command = [
        sys.executable,
        str(taskctl),
        "--root",
        str(root),
        "hook-audit",
        "--source",
        source,
        "--host",
        "dsh",
        "--intent",
        intent,
    ]
    if session_id:
        command.extend(["--session", session_id])
    if agent_id:
        command.extend(["--agent", agent_id])

    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            cwd=str(root),
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        record["result"] = "timeout"
        write_status(args.status, record)
        warn("taskctl.py hook-audit timed out; evidence for this event is missing")
        print(f"DSH_HOOK_TIMEOUT source={source}")
        return 0
    except OSError as exc:
        record["result"] = f"spawn-error:{exc}"
        write_status(args.status, record)
        warn(f"could not run taskctl.py: {exc}")
        print(f"DSH_HOOK_ERROR spawn source={source}")
        return 0

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        # taskctl returns non-zero when the round is not closeable yet, which is
        # the normal state during live work. It is a report, not a hook failure.
        record["result"] = f"taskctl-exit-{completed.returncode}"
        write_status(args.status, record)
        print(f"DSH_HOOK_RECORDED_WITH_FINDINGS source={source} exit={completed.returncode}")
        if stdout:
            print(stdout)
        return 0

    record["result"] = "recorded"
    write_status(args.status, record)
    summary = next(
        (line for line in stdout.splitlines() if line.startswith("HOOK_")),
        stdout.splitlines()[0] if stdout else "HOOK_RUN_RECORDED",
    )
    print(f"DSH_HOOK_OK source={source} phase={record['phase']} root={root}")
    if summary:
        print(summary)
    if stderr:
        warn(stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
