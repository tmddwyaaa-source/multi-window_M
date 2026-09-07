"""v0.26: verification_policy is the only close-path table; POLICY_CONFLICT rejects forks."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(__file__).resolve().parent / "taskctl.py"


def load_taskctl():
    spec = importlib.util.spec_from_file_location("taskctl_v026", TASKCTL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def write_task(root: Path, *, risk: str, attempt: int = 0, extra: dict | None = None) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".task" / "TASK-010").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-010",
        "owner": "M2",
        "status": "worker_done",
        "risk": risk,
        "attempt": attempt,
        "allowed_paths": [
            "src/ok.py",
            "tests/test_ok.py",
            ".task/TASK-010/worker-report.json",
        ],
        "requirements": [
            {
                "id": "R1",
                "text": "ok",
                "verify": "py -3 tests/test_ok.py",
                "verify_cmd": "py -3 tests/test_ok.py",
            }
        ],
    }
    if extra:
        manifest.update(extra)
    report = {
        "task_id": "TASK-010",
        "window": "M2",
        "status": "worker_done",
        "covered_requirements": ["R1"],
        "evidence": [{"requirement_id": "R1", "path": "src/ok.py", "note": "ok"}],
        "changed_files": [
            "src/ok.py",
            "tests/test_ok.py",
            ".task/TASK-010/worker-report.json",
        ],
        "tests": [{"command": "py -3 tests/test_ok.py", "exit_code": 0}],
        "known_gaps": [],
    }
    (root / ".task" / "TASK-010" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "TASK-010" / "worker-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    mod = load_taskctl()
    short = mod.verification_policy({"risk": "low", "attempt": 0})
    expect(
        "P-pos-short-unique",
        short["conflict"] == ""
        and short["independent_verification"] is False
        and short["allow_worker_done_to_integrated"] is True,
        str(short),
    )
    independent = mod.verification_policy({"risk": "medium", "attempt": 0})
    expect(
        "P-pos-independent-unique",
        independent["conflict"] == ""
        and independent["independent_verification"] is True
        and independent["allow_worker_done_to_integrated"] is False,
        str(independent),
    )
    flagged = mod.verification_policy(
        {"risk": "low", "attempt": 0, "verification_required": True}
    )
    expect(
        "P-pos-flag-independent",
        flagged["independent_verification"] is True and flagged["conflict"] == "",
        str(flagged),
    )
    retried = mod.verification_policy({"risk": "low", "attempt": 2})
    expect(
        "P-pos-attempt-independent",
        retried["independent_verification"] is True and retried["conflict"] == "",
        str(retried),
    )
    bad_risk = mod.verification_policy({"risk": "maybe", "attempt": 0})
    expect(
        "P-neg-invalid-risk",
        "POLICY_CONFLICT" in bad_risk["conflict"],
        str(bad_risk),
    )
    mismatch = mod.verification_policy(
        {"risk": "low", "attempt": 0, "independent_verification": True}
    )
    expect(
        "P-neg-explicit-mismatch",
        "POLICY_CONFLICT" in mismatch["conflict"],
        str(mismatch),
    )
    high_false = mod.verification_policy(
        {"risk": "high", "attempt": 0, "independent_verification": False}
    )
    expect(
        "P-neg-high-claims-short",
        "POLICY_CONFLICT" in high_false["conflict"],
        str(high_false),
    )

    temp = Path(tempfile.mkdtemp(prefix="v026-policy-"))
    write_task(temp, risk="maybe")
    code, out = run(["--root", str(temp), "gate", "TASK-010"], cwd=temp)
    expect(
        "G-neg-policy-conflict",
        code != 0 and "POLICY_CONFLICT" in out,
        out,
    )

    write_task(temp, risk="low", extra={"independent_verification": True})
    code, out = run(
        [
            "--root",
            str(temp),
            "transition",
            "TASK-010",
            "integrated",
            "--actor",
            "M1",
        ],
        cwd=temp,
    )
    expect(
        "T-neg-policy-conflict",
        code != 0 and "POLICY_CONFLICT" in out,
        out,
    )

    write_task(temp, risk="medium")
    (temp / ".task" / "TASK-010" / "verify-report.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-010",
                "reviewer": "C1",
                "result": "pass",
                "checked_requirements": ["R1"],
                "missing": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    code, out = run(
        [
            "--root",
            str(temp),
            "transition",
            "TASK-010",
            "integrated",
            "--actor",
            "M1",
        ],
        cwd=temp,
    )
    expect(
        "T-neg-medium-skip-verifier",
        code != 0 and "independent verification required" in out,
        out,
    )
    print("ALL v0.26 POLICY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
