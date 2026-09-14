"""v0.34: one task keeps its worker owner while a separate C/M window verifies it."""
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


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def write_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / ".task" / "TASK-001").mkdir(parents=True)
    (root / "src" / "ok.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (root / "tests" / "check_ok.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-001",
        "title": "separate verifier dispatch",
        "owner": "C1",
        "track": "feature",
        "risk": "medium",
        "attempt": 0,
        "status": "worker_done",
        "allowed_paths": ["src/ok.py", "tests/check_ok.py", ".task/TASK-001/worker-report.json"],
        "source_refs": [{"id": "S1", "text": "separate C1 worker and C2 verifier", "maps_to": ["R1"]}],
        "requirements": [{"id": "R1", "text": "ok", "verify": "py -3 tests/check_ok.py", "verify_cmd": "py -3 tests/check_ok.py"}],
    }
    worker = {
        "task_id": "TASK-001",
        "window": "C1",
        "status": "worker_done",
        "covered_requirements": ["R1"],
        "evidence": [{"requirement_id": "R1", "path": "src/ok.py", "note": "implemented"}],
        "changed_files": ["src/ok.py", "tests/check_ok.py", ".task/TASK-001/worker-report.json"],
        "tests": [{"command": "py -3 tests/check_ok.py", "exit_code": 0}],
        "known_gaps": [],
    }
    verify = {"task_id": "TASK-001", "reviewer": "C2", "result": "pass", "checked_requirements": ["R1"], "missing": []}
    task_dir = root / ".task" / "TASK-001"
    (task_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (task_dir / "worker-report.json").write_text(json.dumps(worker, indent=2) + "\n", encoding="utf-8")
    (task_dir / "verify-report.json").write_text(json.dumps(verify, indent=2) + "\n", encoding="utf-8")


def read_round(root: Path) -> dict:
    return json.loads((root / ".task" / "round.json").read_text(encoding="utf-8"))


def write_round(root: Path, data: dict) -> None:
    (root / ".task" / "round.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="v034-verifier-"))
    write_project(root)
    code, out = run(
        ["--root", str(root), "round-init", "ROUND-001", "C1", "C2", "--task", "C1=TASK-001", "--verifier", "TASK-001=C2"],
        root,
    )
    expect("V-pos-round-init-separate-roles", code == 0 and "CREATED" in out, out)

    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "verifier", "--window", "C2"], root)
    expect("V-pos-verifier-brief", code == 0 and "窗号: C2" in out and "实现 owner: C1" in out, out)
    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "verifier", "--window", "C1"], root)
    expect("V-neg-wrong-brief-window", code != 0 and "does not match" in out, out)

    code, out = run(["--root", str(root), "gate", "TASK-001"], root)
    expect("V-pos-full-gate", code == 0 and "RESULT PASS" in out, out)

    verify_path = root / ".task" / "TASK-001" / "verify-report.json"
    wrong = json.loads(verify_path.read_text(encoding="utf-8"))
    wrong["reviewer"] = "C1"
    verify_path.write_text(json.dumps(wrong, indent=2) + "\n", encoding="utf-8")
    code, out = run(["--root", str(root), "gate", "TASK-001"], root)
    expect("V-neg-worker-cannot-review", code != 0 and "VERIFIER_ASSIGNMENT_CONFLICT" in out, out)
    wrong["reviewer"] = "C2"
    verify_path.write_text(json.dumps(wrong, indent=2) + "\n", encoding="utf-8")

    data = read_round(root)
    data["verifier_assignments"] = {}
    write_round(root, data)
    code, out = run(["--root", str(root), "gate", "TASK-001"], root)
    expect("V-neg-missing-verifier-assignment", code != 0 and "VERIFIER_ASSIGNMENT_MISSING" in out, out)

    code, out = run(["--root", str(root), "assign-verifier", "TASK-001", "--window", "C2", "--actor", "M1"], root)
    expect("V-pos-assign-verifier", code == 0 and "VERIFIER_ASSIGNED" in out, out)
    data = read_round(root)
    data["tasks"] = {"C1": [], "C2": ["TASK-001"]}
    write_round(root, data)
    code, out = run(["--root", str(root), "gate", "TASK-001"], root)
    expect("V-neg-worker-route-must-match-owner", code != 0 and "WORKER_ASSIGNMENT_CONFLICT" in out, out)
    code, out = run(["--root", str(root), "sync-worker-route", "TASK-001", "--actor", "M1"], root)
    expect("V-pos-sync-worker-route", code == 0 and "WORKER_ROUTE_SYNCED" in out, out)
    code, out = run(["--root", str(root), "transition", "TASK-001", "verifying", "--actor", "verifier"], root)
    expect("V-pos-verifying", code == 0 and "STATUS_CHANGED" in out, out)
    code, out = run(["--root", str(root), "transition", "TASK-001", "verified", "--actor", "verifier"], root)
    expect("V-pos-verified", code == 0 and "STATUS_CHANGED" in out, out)
    code, out = run(["--root", str(root), "receipt", "C1"], root)
    expect("V-pos-worker-receipt", code == 0 and "RECEIPT" in out, out)
    code, out = run(["--root", str(root), "receipt", "C2", "--role", "verifier"], root)
    expect("V-pos-verifier-receipt", code == 0 and "RECEIPT" in out, out)
    code, out = run(["--root", str(root), "request-check"], root)
    expect("V-pos-request-check", code == 0 and "CHECK_REQUESTED" in out, out)
    code, out = run(["--root", str(root), "audit-round"], root)
    expect("V-pos-round-audit", code == 0 and "ROUND_READY_TO_CLOSE" in out, out)
    print("ALL v0.34 VERIFIER ASSIGNMENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
