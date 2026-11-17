"""DSH bridge: payload -> ledger pair, always exit 0, never mutate status.

Covers the dsh half of the host evidence contract:
  - one hook event writes exactly one phase entry
  - two events sharing a session id form the complete pair audit-round needs
  - a malformed or empty payload still exits 0 and writes nothing
  - no `.task/` writes nothing (the skill stays inert in foreign projects)
  - the bridge never mutates task status
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
TASKCTL = HERE / "taskctl.py"
BRIDGE = HERE / "dsh-hook-bridge.py"


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run_bridge(
    args: list[str], stdin: str = "", env: Optional[dict] = None
) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    if env:
        environment.update(env)
    completed = subprocess.run(
        [sys.executable, str(BRIDGE), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def run_taskctl(args: list[str]) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


OK_COMMAND = 'py -3 -c "raise SystemExit(0)"'


def seed_project(root: Path, *, hook_supervision: bool) -> None:
    (root / ".task" / "TASK-001" / "evidence").mkdir(parents=True, exist_ok=True)
    (root / ".task" / "TASK-001" / "evidence" / "notes.md").write_text(
        "seed evidence\n", encoding="utf-8"
    )
    (root / ".task" / "TASK-001" / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-001",
                "owner": "M2",
                "status": "worker_done",
                "risk": "low",
                "attempt": 0,
                "allowed_paths": [
                    "src/",
                    ".task/TASK-001/worker-report.json",
                    ".task/TASK-001/evidence/",
                ],
                "source_refs": [{"id": "S1", "text": "seed requirement", "maps_to": ["R1"]}],
                "requirements": [{"id": "R1", "text": "x", "verify": OK_COMMAND, "verify_cmd": OK_COMMAND}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".task" / "TASK-001" / "worker-report.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-001",
                "window": "M2",
                "status": "worker_done",
                "covered_requirements": ["R1"],
                "evidence": [
                    {"requirement_id": "R1", "path": ".task/TASK-001/evidence/notes.md", "note": "seed"}
                ],
                "changed_files": ["src/x.py", ".task/TASK-001/worker-report.json", ".task/TASK-001/evidence/notes.md"],
                "tests": [{"command": OK_COMMAND, "exit_code": 0}],
                "known_gaps": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".task" / "round.json").write_text(
        json.dumps(
            {
                "round_id": "ROUND-001",
                "expected_windows": ["M2"],
                "receipts": ["M2"],
                "window_status": {"M2": "worker_done"},
                "tasks": {"M2": ["TASK-001"]},
                "gears": {"capability": "default", "collaboration": "P", "capability_confirmed": False},
                "hook_supervision": hook_supervision,
                "check_requested": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="m032-dsh-"))

    empty = temp / "empty"
    empty.mkdir()
    code, out = run_bridge(["--root", str(empty)], stdin='{"hook_event_name":"Stop","session_id":"s1"}')
    expect(
        "D-pos-no-task-exit-0",
        code == 0 and "HOOK_AUDIT_SKIP" in out,
        f"code={code} out={out}",
    )
    expect(
        "D-neg-no-ledger-without-task",
        not (empty / ".task" / "dsh-runs.jsonl").exists(),
        "ledger written without .task/",
    )

    code, out = run_bridge(["--root", str(empty)], stdin="")
    expect("D-pos-empty-stdin-exit-0", code == 0, f"code={code} out={out}")

    code, out = run_bridge(["--root", str(empty)], stdin="{not json")
    expect(
        "D-pos-bad-json-exit-0",
        code == 0 and "not JSON" in out,
        f"code={code} out={out}",
    )

    project = temp / "project"
    seed_project(project, hook_supervision=False)
    status_file = project / ".task" / "dsh-hook-status.json"

    code, out = run_bridge(
        ["--root", str(project), "--status", str(status_file)],
        stdin=json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-A",
                "cwd": str(project),
                "transcript_path": "",
            }
        ),
        env={"MULTI_WINDOW_TASKCTL": str(TASKCTL)},
    )
    entries = ledger(project / ".task" / "dsh-runs.jsonl")
    expect(
        "D-pos-one-call-writes-complete-pair",
        code == 0
        and len(entries) == 2
        and {entry["phase"] for entry in entries} == {"start", "end"}
        and {entry["run_id"] for entry in entries} == {"sess-A"}
        and {entry["source"] for entry in entries} == {"dsh-stop"}
        and entries[0]["host"] == "dsh"
        and entries[0]["session_id"] == "sess-A"
        and entries[0]["project_root"] == str(project),
        f"code={code} entries={json.dumps(entries)} out={out}",
    )
    expect(
        "D-pos-not-in-cursor-ledger",
        not (project / ".task" / "hook-runs.jsonl").exists(),
        "dsh entry landed in hook-runs.jsonl",
    )
    expect("D-pos-status-file", status_file.exists(), "status file not written")
    expect(
        "D-pos-pair-intent",
        entries[0]["intent"] == "pair" and entries[0]["audit_result"] == "pending",
        json.dumps(entries[0]),
    )
    expect(
        "D-pos-both-phases-one-session",
        entries[0]["run_id"] == entries[1]["run_id"] == "sess-A",
        json.dumps(entries),
    )

    code, out = run_bridge(
        ["--root", str(project)],
        stdin=json.dumps(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "sess-B",
                "agent_id": "agent-7",
                "cwd": str(project),
            }
        ),
        env={"MULTI_WINDOW_TASKCTL": str(TASKCTL)},
    )
    entries = ledger(project / ".task" / "dsh-runs.jsonl")
    subagent_rows = [entry for entry in entries if entry["source"] == "dsh-subagent-end"]
    expect(
        "D-pos-subagent-pair-and-id",
        code == 0
        and len(subagent_rows) == 2
        and {entry["phase"] for entry in subagent_rows} == {"start", "end"}
        and subagent_rows[0]["agent_id"] == "agent-7"
        and subagent_rows[0]["event"] == "subagent-stop",
        f"code={code} rows={json.dumps(subagent_rows)} out={out}",
    )

    code, out = run_bridge(
        ["--root", str(project)],
        stdin=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "sess-A", "cwd": str(project)}
        ),
        env={"MULTI_WINDOW_TASKCTL": str(TASKCTL)},
    )
    entries = ledger(project / ".task" / "dsh-runs.jsonl")
    session_rows = [entry for entry in entries if entry["source"] == "dsh-session-start"]
    expect(
        "D-pos-record-intent-writes-one-entry",
        code == 0 and len(session_rows) == 1 and session_rows[0]["intent"] == "record",
        f"code={code} rows={json.dumps(session_rows)} out={out}",
    )

    supervised = temp / "supervised"
    seed_project(supervised, hook_supervision=True)
    code, out = run_taskctl(["--root", str(supervised), "audit-round"])
    expect(
        "D-neg-supervision-without-evidence",
        "HOOK_EVIDENCE_MISSING" in out,
        f"code={code} out={out}",
    )

    run_bridge(
        ["--root", str(supervised)],
        stdin=json.dumps({"hook_event_name": "SessionStart", "session_id": "sess-C", "cwd": str(supervised)}),
        env={"MULTI_WINDOW_TASKCTL": str(TASKCTL)},
    )
    code, out = run_taskctl(["--root", str(supervised), "audit-round"])
    expect(
        "D-neg-record-only-is-not-evidence",
        "HOOK_EVIDENCE_MISSING" in out,
        f"code={code} out={out}",
    )

    run_bridge(
        ["--root", str(supervised)],
        stdin=json.dumps({"hook_event_name": "Stop", "session_id": "sess-C", "cwd": str(supervised)}),
        env={"MULTI_WINDOW_TASKCTL": str(TASKCTL)},
    )
    code, out = run_taskctl(["--root", str(supervised), "audit-round"])
    expect(
        "D-pos-supervision-satisfied-by-one-dsh-stop",
        "HOOK_EVIDENCE_MISSING" not in out,
        f"code={code} out={out}",
    )

    status_before = json.loads(
        (project / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8")
    )["status"]
    round_before = json.loads((project / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "D-neg-no-status-mutation",
        status_before == "worker_done" and round_before["window_status"] == {"M2": "worker_done"},
        f"status={status_before} round={json.dumps(round_before.get('window_status'))}",
    )

    code, out = run_taskctl(["--root", str(project), "hook-audit", "--source", "manual", "--host", "dsh"])
    expect(
        "D-neg-dsh-host-requires-dsh-source",
        code != 0 and "requires a dsh-* source" in out,
        f"code={code} out={out}",
    )

    print("ALL DSH BRIDGE CHECKS PASSED")
    print(f"temp: {temp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
