"""v0.30: gears must not fake high gear; hook_supervision needs host-stop evidence."""
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


def write_ready(
    root: Path,
    *,
    risk: str = "low",
    gears: dict | None = None,
    hook_supervision: bool = False,
    subagents: list | None = None,
    extra_manifest: dict | None = None,
) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".task" / "TASK-001").mkdir(parents=True, exist_ok=True)
    (root / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-001",
        "owner": "C1",
        "status": "worker_done",
        "risk": risk,
        "attempt": 0,
        "allowed_paths": ["src/ok.py", "tests/test_ok.py", ".task/TASK-001/worker-report.json"],
        "source_refs": [{"id": "S1", "text": "ok.py", "maps_to": ["R1"]}],
        "requirements": [
            {
                "id": "R1",
                "text": "ok",
                "verify": "py -3 tests/test_ok.py",
                "verify_cmd": "py -3 tests/test_ok.py",
            }
        ],
    }
    if extra_manifest:
        manifest.update(extra_manifest)
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
        "hook_supervision": hook_supervision,
    }
    if gears is not None:
        round_data["gears"] = gears
    if subagents is not None:
        round_data["subagents"] = subagents
    (root / ".task" / "TASK-001" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "TASK-001" / "worker-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (root / ".task" / "round.json").write_text(json.dumps(round_data, indent=2) + "\n", encoding="utf-8")


def write_hook_pair(root: Path, *, source: str, host: str, run_id: str = "run-1") -> None:
    path = root / ".task" / "hook-runs.jsonl"
    rows = [
        {"run_id": run_id, "phase": "start", "source": source, "host": host},
        {"run_id": run_id, "phase": "end", "source": source, "host": host, "exit_code": 0},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def subagent(run_id: str, role: str = "worker", paths: list[str] | None = None, window: str = "C1") -> dict:
    return {
        "run_id": run_id,
        "task_id": "TASK-001",
        "window": window,
        "role": role,
        "allowed_paths": paths or ["src/ok.py"],
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="v030-gear-"))

    write_ready(root)
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect("G-pos-default-close", code == 0 and "ROUND_READY_TO_CLOSE" in out, out)

    write_ready(
        root,
        gears={"capability": "bonus", "collaboration": "P", "capability_confirmed": False},
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "G-neg-fake-bonus",
        code != 0 and "GEAR_VIOLATION" in out and "ROUND_READY_TO_CLOSE" not in out,
        out,
    )

    write_ready(
        root,
        gears={"capability": "default", "collaboration": "A", "capability_confirmed": False},
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "G-neg-collab-A-unconfirmed",
        code != 0 and "GEAR_VIOLATION" in out and "collaboration A" in out,
        out,
    )

    write_ready(
        root,
        gears={"capability": "bonus", "collaboration": "A", "capability_confirmed": True},
        risk="medium",
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "G-neg-bonus-skips-verify",
        code != 0
        and "VERIFY_REQUIRED" in out
        and "FULL_GATE_FAIL" in out
        and "ROUND_READY_TO_CLOSE" not in out
        and "GEAR_VIOLATION" not in out,
        out,
    )

    write_ready(root, hook_supervision=True)
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "H-neg-hook-missing",
        code != 0 and "HOOK_EVIDENCE_MISSING" in out and "ROUND_READY_TO_CLOSE" not in out,
        out,
    )

    write_ready(root, hook_supervision=True)
    write_hook_pair(root, source="manual", host="manual")
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "H-neg-manual-not-evidence",
        code != 0 and "HOOK_EVIDENCE_MISSING" in out and "manual" in out,
        out,
    )

    write_ready(root, hook_supervision=True)
    write_hook_pair(root, source="cursor-stop", host="cursor")
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "H-pos-host-stop-pair",
        code == 0 and "ROUND_READY_TO_CLOSE" in out,
        out,
    )

    write_ready(root, hook_supervision=True)
    write_hook_pair(root, source="cursor-stop", host="cursor")
    code, out = run(["--root", str(root), "hook-audit", "--source", "manual"], cwd=root)
    expect(
        "H-pos-hook-audit-no-mutate",
        "HOOK_RUN_RECORDED" in out,
        out,
    )
    status = json.loads((root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8"))["status"]
    expect("H-pos-hook-still-pending-status", status == "worker_done", status)

    write_ready(
        root,
        subagents=[subagent("run-a"), subagent("run-b", paths=["src/ok.py"])],
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "P-neg-concurrent-paths",
        code != 0 and "PARALLEL_FAIL" in out and "concurrent paths" in out,
        out,
    )

    write_ready(
        root,
        subagents=[subagent("dup"), subagent("dup", paths=["tests/test_ok.py"])],
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "P-neg-duplicate-run-id",
        code != 0 and "PARALLEL_FAIL" in out and "duplicate subagent run_id" in out,
        out,
    )

    write_ready(
        root,
        subagents=[
            subagent("w1", role="worker"),
            subagent("v1", role="verifier", paths=["tests/test_ok.py"]),
        ],
    )
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "P-neg-worker-as-verifier",
        code != 0 and "PARALLEL_FAIL" in out and "worker cannot also be verifier" in out,
        out,
    )

    write_ready(root, subagents=[subagent("out", paths=["docs/secret.md"])])
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "P-neg-path-overflow",
        code != 0 and "PARALLEL_FAIL" in out and "outside" in out,
        out,
    )

    write_ready(root, subagents=[subagent("ok1")])
    code, out = run(["--root", str(root), "audit-round"], cwd=root)
    expect(
        "P-pos-bound-subagent",
        code == 0 and "ROUND_READY_TO_CLOSE" in out,
        out,
    )

    print("ALL v0.30 GEAR/HOOK/PARALLEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
