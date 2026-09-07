"""v0.29: source_refs must map every user ask to an R item with a verify command."""
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


def write_mapped(root: Path, *, extra_source: dict | None = None, round_sources: list | None = None) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".task" / "TASK-001").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    refs = [{"id": "S1", "text": "user asked for ok.py", "maps_to": ["R1"]}]
    if extra_source:
        refs.append(extra_source)
    manifest = {
        "task_id": "TASK-001",
        "owner": "C1",
        "status": "worker_done",
        "risk": "low",
        "attempt": 0,
        "allowed_paths": ["src/ok.py", "tests/test_ok.py", ".task/TASK-001/worker-report.json"],
        "source_refs": refs,
        "requirements": [
            {
                "id": "R1",
                "text": "ok",
                "verify": "py -3 tests/test_ok.py",
                "verify_cmd": "py -3 tests/test_ok.py",
            }
        ],
    }
    report = {
        "task_id": "TASK-001",
        "window": "C1",
        "status": "worker_done",
        "covered_requirements": ["R1"],
        "evidence": [{"requirement_id": "R1", "path": "src/ok.py", "note": "ok"}],
        "changed_files": ["src/ok.py", "tests/test_ok.py", ".task/TASK-001/worker-report.json"],
        "tests": [{"command": "py -3 tests/test_ok.py", "exit_code": 0}],
        "known_gaps": [],
    }
    round_data = {
        "round_id": "ROUND-001",
        "expected_windows": ["C1"],
        "receipts": ["C1"],
        "window_status": {"C1": "worker_done"},
        "tasks": {"C1": ["TASK-001"]},
        "check_requested": True,
    }
    if round_sources is not None:
        round_data["source_requirements"] = round_sources
    (root / ".task" / "TASK-001" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "TASK-001" / "worker-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "round.json").write_text(json.dumps(round_data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="v029-cov-"))
    write_mapped(root)
    manifest_path = root / ".task" / "TASK-001" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    del data["source_refs"]
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    code, out = run(["--root", str(root), "gate", "TASK-001"], cwd=root)
    expect(
        "C-neg-missing-source-refs",
        code != 0 and "REQUIREMENT_COVERAGE_FAIL" in out and "missing source_refs" in out,
        out,
    )

    write_mapped(root, extra_source={"id": "S2", "text": "also add a logger", "maps_to": []})
    code, out = run(["--root", str(root), "gate", "TASK-001"], cwd=root)
    expect(
        "C-neg-unmapped-source",
        code != 0 and "REQUIREMENT_COVERAGE_FAIL" in out and "S2" in out,
        out,
    )

    write_mapped(root)
    code, out = run(["--root", str(root), "gate", "TASK-001"], cwd=root)
    expect(
        "C-pos-mapped-gate",
        code == 0 and "RESULT PASS" in out,
        out,
    )

    write_mapped(
        root,
        round_sources=[
            {"id": "S1", "text": "ok.py"},
            {"id": "S2", "text": "logger that was only mentioned in chat"},
        ],
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "C-neg-round-unmapped",
        code != 0 and "REQUIREMENT_COVERAGE_FAIL" in out and "S2" in out,
        out,
    )

    write_mapped(root, round_sources=[{"id": "S1", "text": "ok.py"}])
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "C-pos-round-mapped",
        code == 0 and "ROUND_READY_TO_CLOSE" in out,
        out,
    )
    print("ALL v0.29 REQUIREMENT COVERAGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
