"""v0.27: brief and handoff are copy-ready; missing task / illegal role refused."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(__file__).resolve().parent / "taskctl.py"


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def write_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "docs" / "BLOCKERS").mkdir(parents=True)
    (root / ".task" / "TASK-001").mkdir(parents=True)
    (root / "src" / "ping.py").write_text('PING = "ok"\n', encoding="utf-8")
    (root / "tests" / "check_ping.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-001",
        "title": "ping constant",
        "owner": "C1",
        "track": "feature",
        "risk": "low",
        "attempt": 0,
        "status": "in_progress",
        "allowed_paths": ["src/ping.py", "tests/check_ping.py", ".task/TASK-001/worker-report.json"],
        "requirements": [
            {
                "id": "R1",
                "text": "PING is ok",
                "verify": "py -3 tests/check_ping.py",
                "verify_cmd": "py -3 tests/check_ping.py",
            }
        ],
        "source_refs": [{"id": "S1", "text": "PING is ok", "maps_to": ["R1"]}],
    }
    round_data = {
        "round_id": "ROUND-001",
        "expected_windows": ["C1"],
        "receipts": [],
        "window_status": {"C1": "in_progress"},
        "tasks": {"C1": ["TASK-001"]},
        "check_requested": False,
    }
    (root / ".task" / "TASK-001" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "round.json").write_text(json.dumps(round_data, indent=2) + "\n", encoding="utf-8")
    (root / "docs" / "BLOCKERS" / "C1-stuck.md").write_text("# stuck\n", encoding="utf-8")
    hook = {
        "run_id": "abc",
        "phase": "end",
        "host": "cursor",
        "source": "cursor-stop",
        "audit_result": "ok",
    }
    (root / ".task" / "hook-runs.jsonl").write_text(json.dumps(hook) + "\n", encoding="utf-8")


def main() -> int:
    empty = Path(tempfile.mkdtemp(prefix="v027-empty-"))
    code, out = run(["--root", str(empty), "brief", "TASK-001", "--role", "worker"], cwd=empty)
    expect(
        "B-neg-missing-task",
        code != 0 and "BRIEF_FAIL: missing task" in out,
        out,
    )
    code, out = run(["--root", str(empty), "handoff"], cwd=empty)
    expect(
        "H-neg-no-task-dir",
        code != 0 and "HANDOFF_FAIL: no .task" in out,
        out,
    )

    root = Path(tempfile.mkdtemp(prefix="v027-brief-"))
    write_project(root)
    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "boss"], cwd=root)
    expect(
        "B-neg-illegal-role",
        code != 0 and "BRIEF_FAIL: illegal role" in out,
        out,
    )

    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "worker"], cwd=root)
    expect(
        "B-pos-worker",
        code == 0
        and "BRIEF TASK-001 role=worker" in out
        and "src/ping.py" in out
        and "R1: PING is ok" in out
        and "py -3 tests/check_ping.py" in out
        and "worker-report.json" in out
        and "短路径" in out
        and "窗号: C1" in out,
        out,
    )
    expect("B-pos-no-mutate-worker", (root / ".task" / "TASK-001" / "manifest.json").exists(), "manifest missing")

    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "scout"], cwd=root)
    expect(
        "B-pos-scout",
        code == 0 and "role=scout" in out and "只读调查" in out and "禁止改代码" in out,
        out,
    )
    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "verifier"], cwd=root)
    expect(
        "B-pos-verifier",
        code == 0 and "role=verifier" in out and "verify-report.json" in out and "reviewer" in out,
        out,
    )

    before = (root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8")
    code, out = run(["--root", str(root), "handoff"], cwd=root)
    after = (root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8")
    expect(
        "H-pos-handoff",
        code == 0
        and out.startswith("HANDOFF")
        and "TASK-001" in out
        and "attempt=0" in out
        and "docs/BLOCKERS/C1-stuck.md" in out
        and "host=cursor" in out
        and "下一步" in out
        and "in_progress" in out,
        out,
    )
    expect("H-pos-no-mutate", before == after, "handoff mutated manifest")
    print("ALL v0.27 BRIEF/HANDOFF CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
