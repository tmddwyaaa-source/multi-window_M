"""v0.28: status --markdown is the only status table; handwritten Registry is not authority."""
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


def write_project(root: Path, task_status: str = "pending") -> None:
    (root / ".task" / "TASK-001").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    manifest = {
        "task_id": "TASK-001",
        "title": "ping",
        "owner": "C1",
        "status": task_status,
        "risk": "low",
        "attempt": 0,
        "allowed_paths": ["src/"],
        "requirements": [{"id": "R1", "text": "x", "verify": "y"}],
    }
    round_data = {
        "round_id": "ROUND-001",
        "expected_windows": ["C1"],
        "receipts": ["C1"] if task_status == "worker_done" else [],
        "window_status": {"C1": "worker_done" if task_status == "worker_done" else "pending"},
        "tasks": {"C1": ["TASK-001"]},
        "check_requested": False,
    }
    (root / ".task" / "TASK-001" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "round.json").write_text(json.dumps(round_data, indent=2) + "\n", encoding="utf-8")
    (root / "docs" / "MODULE-REGISTRY.md").write_text(
        "| C1 | hello | C1 | done |\n",
        encoding="utf-8",
    )
    (root / "docs" / "RECEIPT-LOG.md").write_text(
        "**结论**：done\n",
        encoding="utf-8",
    )


def main() -> int:
    empty = Path(tempfile.mkdtemp(prefix="v028-empty-"))
    code, out = run(["--root", str(empty), "status", "--markdown"], cwd=empty)
    expect(
        "S-neg-no-task-markdown",
        code != 0 and "STATUS_FAIL: no .task" in out,
        out,
    )
    code, out = run(["--root", str(empty), "status", "--write"], cwd=empty)
    expect(
        "S-neg-no-task-write",
        code != 0 and "STATUS_FAIL: no .task" in out,
        out,
    )
    expect(
        "S-neg-no-write-file",
        not (empty / "docs" / "TASK-STATUS.md").exists(),
        "wrote TASK-STATUS.md without .task",
    )

    root = Path(tempfile.mkdtemp(prefix="v028-status-"))
    write_project(root, "pending")
    before = (root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8")
    code, out = run(["--root", str(root), "status", "--markdown"], cwd=root)
    expect(
        "S-pos-from-task-not-registry",
        code == 0
        and "taskctl:generated-status" in out
        and "TASK-001" in out
        and "| C1 |" in out
        and "pending" in out
        and "权威存储" in out,
        out,
    )
    expect(
        "S-pos-ignore-handwritten-done",
        "TASK-001" in out and "| TASK-001 |" in out,
        out,
    )
    # Registry says done; generated table must still show pending for the task cell.
    expect(
        "S-pos-task-cell-pending",
        "| TASK-001 | pending |" in out or "| C1 | pending | no | TASK-001 | pending |" in out,
        out,
    )
    after = (root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8")
    expect("S-pos-no-mutate", before == after, "status --markdown mutated manifest")

    code, out = run(["--root", str(root), "status", "--markdown", "--write"], cwd=root)
    written = root / "docs" / "TASK-STATUS.md"
    expect(
        "S-pos-write",
        code == 0 and "STATUS_WRITTEN docs/TASK-STATUS.md" in out and written.exists(),
        out,
    )
    body = written.read_text(encoding="utf-8")
    expect(
        "S-pos-written-pending",
        "taskctl:generated-status" in body and "pending" in body and "RECEIPT 摘要" in body,
        body,
    )
    handwritten = (root / "docs" / "MODULE-REGISTRY.md").read_text(encoding="utf-8")
    expect("S-pos-registry-untouched", "| C1 | hello | C1 | done |" in handwritten, handwritten)

    write_project(root, "worker_done")
    code, out = run(["--root", str(root), "status", "--write"], cwd=root)
    body = written.read_text(encoding="utf-8")
    expect(
        "S-pos-rerender-follows-task",
        code == 0 and "worker_done" in body and "yes" in body,
        body,
    )
    print("ALL v0.28 STATUS MARKDOWN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
