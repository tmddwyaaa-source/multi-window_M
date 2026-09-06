"""v0.24 hook observability: start/end, host, 100-line cap, no status mutation."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(r"C:\Users\user\.cursor\skills\multi-window_M\scripts\taskctl.py")


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def load_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="v024-hook-"))
    (temp / ".task").mkdir()
    (temp / ".task" / "TASK-KEEP").mkdir()
    (temp / ".task" / "TASK-KEEP" / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-KEEP",
                "owner": "M2",
                "status": "pending",
                "risk": "low",
                "attempt": 0,
                "allowed_paths": ["src/"],
                "requirements": [{"id": "R1", "text": "x", "verify": "y"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    code, out = run(["--root", str(temp / "none"), "hook-audit", "--source", "manual"])
    expect("H-neg-no-task", code == 0 and "HOOK_AUDIT_SKIP" in out, out)
    expect("H-neg-no-file", not (temp / "none" / ".task" / "hook-runs.jsonl").exists(), "jsonl created without .task")

    code, out = run(
        ["--root", str(temp), "hook-audit", "--source", "manual", "--host", "cursor"],
        cwd=temp,
    )
    log = temp / ".task" / "hook-runs.jsonl"
    expect("H-pos-recorded", code == 0 and "HOOK_RUN_RECORDED" in out and "host=cursor" in out, out)
    rows = load_lines(log)
    expect("H-pos-pair", len(rows) == 2 and rows[0]["phase"] == "start" and rows[1]["phase"] == "end", json.dumps(rows))
    expect("H-pos-run-id", rows[0]["run_id"] == rows[1]["run_id"] and bool(rows[0]["run_id"]), json.dumps(rows))
    expect(
        "H-pos-fields",
        rows[1]["host"] == "cursor"
        and rows[1]["source"] == "manual"
        and rows[1]["event"] == "stop"
        and rows[1]["project_root"] == str(temp)
        and rows[1]["exit_code"] == 0
        and "audit_result" in rows[1],
        json.dumps(rows[1]),
    )
    status_before = json.loads((temp / ".task" / "TASK-KEEP" / "manifest.json").read_text(encoding="utf-8"))["status"]
    expect("H-neg-no-transition", status_before == "pending", status_before)

    for _ in range(60):
        run(["--root", str(temp), "hook-audit", "--source", "manual"], cwd=temp)
    rows = load_lines(log)
    expect("H-pos-cap-100", len(rows) == 100, f"lines={len(rows)}")
    expect("H-pos-cap-ends-end", rows[-1]["phase"] == "end", json.dumps(rows[-1]))

    print("ALL v0.24 HOOK CHECKS PASSED")
    print(f"temp: {temp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
