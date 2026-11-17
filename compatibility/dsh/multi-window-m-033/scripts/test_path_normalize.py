"""v0.24 path normalize: keep .task/; compare git vs claimed with the same rule."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(__file__).resolve().parent / "taskctl.py"


def load_taskctl():
    spec = importlib.util.spec_from_file_location("taskctl_v024", TASKCTL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run_gate(root: Path, task_id: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), "--root", str(root), "gate", task_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(root),
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def write_task(root: Path, changed_files: list[str], allowed_paths: list[str] | None = None) -> None:
    (root / "src" / "lab").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".task" / "TASK-004").mkdir(parents=True, exist_ok=True)
    (root / "src" / "lab" / "clip.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_clip.py").write_text("print('RESULT PASS')\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-004",
        "owner": "M2",
        "status": "worker_done",
        "risk": "low",
        "attempt": 0,
        "allowed_paths": allowed_paths
        or [
            "src/lab/clip.py",
            "tests/test_clip.py",
            ".task/TASK-004/worker-report.json",
        ],
        "requirements": [
            {
                "id": "R1",
                "text": "clip",
                "verify": "py -3 tests/test_clip.py",
                "verify_cmd": "py -3 tests/test_clip.py",
            }
        ],
        "source_refs": [{"id": "S1", "text": "clip", "maps_to": ["R1"]}],
    }
    report = {
        "task_id": "TASK-004",
        "window": "M2",
        "status": "worker_done",
        "covered_requirements": ["R1"],
        "evidence": [{"requirement_id": "R1", "path": "src/lab/clip.py", "note": "clip"}],
        "changed_files": changed_files,
        "tests": [{"command": "py -3 tests/test_clip.py", "exit_code": 0}],
        "known_gaps": [],
    }
    (root / ".task" / "TASK-004" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / ".task" / "TASK-004" / "worker-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    mod = load_taskctl()
    expect(
        "N-pos-dot-task",
        mod.normalized_path(".task/TASK-004/worker-report.json") == ".task/TASK-004/worker-report.json",
        mod.normalized_path(".task/TASK-004/worker-report.json"),
    )
    expect("N-pos-dot-slash", mod.normalized_path("./src/lab/clip.py") == "src/lab/clip.py", mod.normalized_path("./src/lab/clip.py"))
    expect("N-pos-backslash", mod.normalized_path("src\\lab\\clip.py") == "src/lab/clip.py", mod.normalized_path("src\\lab\\clip.py"))
    expect("N-neg-parent", mod.normalized_path("../secret") == "../secret", mod.normalized_path("../secret"))
    expect(
        "N-pos-allowed",
        mod.path_allowed(".task/TASK-004/worker-report.json", [".task/TASK-004/worker-report.json"]),
        "path_allowed failed for .task report",
    )

    temp = Path(tempfile.mkdtemp(prefix="v024-path-"))
    (temp / ".gitignore").write_text(".task/hook-runs.jsonl\n", encoding="utf-8")
    git(temp, "init")
    git(temp, "config", "user.email", "test@example.com")
    git(temp, "config", "user.name", "test")
    git(temp, "add", ".gitignore")
    git(temp, "commit", "-m", "baseline")

    write_task(
        temp,
        [
            "src/lab/clip.py",
            "tests/test_clip.py",
            ".task/TASK-004/worker-report.json",
        ],
    )
    code, out = run_gate(temp, "TASK-004")
    expect("G-pos-dot-task-declared", code == 0 and "RESULT PASS" in out, out)

    write_task(
        temp,
        [
            "src/lab/clip.py",
            ".task/TASK-004/worker-report.json",
        ],
    )
    code, out = run_gate(temp, "TASK-004")
    expect("G-neg-omit-test", code != 0 and "tests/test_clip.py" in out, out)

    owned_root = Path(tempfile.mkdtemp(prefix="v025-owned-"))
    (owned_root / ".gitignore").write_text(".task/hook-runs.jsonl\n", encoding="utf-8")
    git(owned_root, "init")
    git(owned_root, "config", "user.email", "test@example.com")
    git(owned_root, "config", "user.name", "test")
    git(owned_root, "add", ".gitignore")
    git(owned_root, "commit", "-m", "baseline")
    write_task(
        owned_root,
        [
            "src/lab/clip.py",
            "tests/test_clip.py",
            ".task/TASK-004/worker-report.json",
        ],
        allowed_paths=["src/lab/clip.py", "tests/test_clip.py", ".task/TASK-004/"],
    )
    code, out = run_gate(owned_root, "TASK-004")
    expect(
        "G-pos-skip-manifest",
        code == 0 and "RESULT PASS" in out and "manifest.json" not in out,
        out,
    )
    (owned_root / ".task" / "TASK-004" / "verify-report.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-004",
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
    code, out = run_gate(owned_root, "TASK-004")
    expect(
        "G-pos-skip-verify-report",
        code == 0 and "RESULT PASS" in out and "verify-report.json" not in out,
        out,
    )
    expect(
        "G-pos-owned-set",
        ".task/TASK-004/manifest.json" in mod.gate_owned_paths("TASK-004")
        and ".task/TASK-004/rerun.json" in mod.gate_owned_paths("TASK-004")
        and ".task/TASK-004/verify-report.json" in mod.gate_owned_paths("TASK-004"),
        str(mod.gate_owned_paths("TASK-004")),
    )
    print("ALL v0.24 PATH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
