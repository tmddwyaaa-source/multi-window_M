"""v0.33: same-block retry ladder keeps an auditable owner handoff."""
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


def run(args: list[str], root: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(root),
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def manifest_path(root: Path) -> Path:
    return root / ".task" / "TASK-001" / "manifest.json"


def load_manifest(root: Path) -> dict:
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))


def save_manifest(root: Path, manifest: dict) -> None:
    manifest_path(root).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_project(root: Path) -> None:
    directory = root / ".task" / "TASK-001"
    directory.mkdir(parents=True)
    manifest = {
        "task_id": "TASK-001",
        "owner": "M2",
        "track": "feature",
        "risk": "medium",
        "attempt": 0,
        "status": "worker_done",
        "status_history": [],
        "assignment_history": [
            {"from": None, "to": "M2", "at": "2026-09-13T00:00:00+00:00", "reason": "initial"}
        ],
        "block_history": [],
        "allowed_paths": ["src/"],
        "source_refs": [{"id": "S1", "text": "retry", "maps_to": ["R1"]}],
        "requirements": [{"id": "R1", "text": "retry", "verify": "echo ok"}],
    }
    save_manifest(root, manifest)
    round_data = {
        "round_id": "ROUND-001",
        "expected_windows": ["M2", "C1"],
        "receipts": [],
        "window_status": {"M2": "worker_done", "C1": "pending"},
        "tasks": {"M2": ["TASK-001"], "C1": []},
        "check_requested": False,
    }
    (root / ".task" / "round.json").write_text(
        json.dumps(round_data, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="v033-retry-"))
    write_project(root)

    code, out = run(
        ["reopen", "TASK-001", "--block-id", "BLOCK-UI", "--reason", "first fail", "--actor", "M1"],
        root,
    )
    manifest = load_manifest(root)
    expect(
        "R-pos-first-reuses-owner",
        code == 0
        and "failures=1" in out
        and manifest["owner"] == "M2"
        and manifest["attempt"] == 1
        and manifest["block_history"][-1]["block_id"] == "BLOCK-UI",
        out,
    )

    manifest["status"] = "worker_done"
    save_manifest(root, manifest)
    code, out = run(
        ["reopen", "TASK-001", "--block-id", "BLOCK-OTHER", "--reason", "renamed", "--actor", "M1"],
        root,
    )
    expect(
        "R-neg-root-cause-change-needs-evidence",
        code != 0 and "BLOCK_ID_CHANGE_REQUIRES_EVIDENCE" in out and load_manifest(root)["attempt"] == 1,
        out,
    )

    code, out = run(
        ["reopen", "TASK-001", "--block-id", "BLOCK-UI", "--reason", "second fail", "--actor", "M1"],
        root,
    )
    manifest = load_manifest(root)
    expect(
        "R-pos-second-demands-reassignment",
        code != 0
        and "REASSIGN_REQUIRED" in out
        and manifest["status"] == "blocked"
        and manifest["reassign_required"] is True
        and manifest["attempt"] == 2
        and (root / "docs" / "BLOCKERS" / "TASK-001-BLOCK-UI.md").exists(),
        out,
    )

    code, out = run(
        ["reassign", "TASK-001", "--owner", "C1", "--reason", "fresh owner", "--actor", "M1"],
        root,
    )
    manifest = load_manifest(root)
    round_data = json.loads((root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "R-pos-owner-history-and-round-move",
        code == 0
        and manifest["owner"] == "C1"
        and manifest["assignment_history"][-1]["from"] == "M2"
        and manifest["assignment_history"][-1]["to"] == "C1"
        and "TASK-001" in round_data["tasks"]["C1"]
        and "TASK-001" not in round_data["tasks"]["M2"],
        out,
    )

    manifest["status"] = "worker_done"
    save_manifest(root, manifest)
    code, out = run(
        ["reopen", "TASK-001", "--block-id", "BLOCK-UI", "--reason", "third fail", "--actor", "M1"],
        root,
    )
    manifest = load_manifest(root)
    expect(
        "R-pos-third-hard-stop",
        code != 0
        and "HARD_STOP" in out
        and manifest["hard_stop"] is True
        and manifest["status"] == "blocked"
        and len(manifest["block_history"]) == 3,
        out,
    )
    print("ALL v0.33 RETRY LADDER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
