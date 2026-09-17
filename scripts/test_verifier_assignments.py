"""v0.36: 一任务两职责（worker 实现 + verifier 独立验收）。

覆盖 Codex 的对齐要求：
  - round.tasks 只表达 worker 路由；round.verifier_assignments 表达验收路由；
  - 缺指派 → VERIFIER_ASSIGNMENT_MISSING；
  - verifier 非本轮窗 / 等于 owner / 等于 worker / 短路径多派 → VERIFIER_ASSIGNMENT_CONFLICT；
  - reviewer 不等于被指派 verifier（冒名验收）→ VERIFIER_ASSIGNMENT_CONFLICT；
  - round.tasks 的 worker 不等于 manifest.owner → WORKER_ASSIGNMENT_CONFLICT；
  - assign-verifier 不改 owner / attempt / block；
  - sync-worker-route 能在验收开始前修复错误主路由。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKCTL = HERE / "taskctl.py"
OK_COMMAND = 'py -3 -c "raise SystemExit(0)"'


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def build(root: Path, *, verifier: str | None = "C2", reviewer: str = "C2",
          route: str = "C1", risk: str = "medium", status: str = "verifier_pending") -> None:
    task = root / ".task" / "TASK-001"
    (task / "evidence").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "ping.py").write_text('PING = "ok"\n', encoding="utf-8")
    (task / "evidence" / "notes.md").write_text("ok\n", encoding="utf-8")

    manifest = {
        "task_id": "TASK-001",
        "title": "ping",
        "owner": "C1",
        "track": "feature",
        "risk": risk,
        "attempt": 0,
        "status": "worker_done",
        "allowed_paths": [
            "src/",
            ".task/TASK-001/worker-report.json",
            ".task/TASK-001/evidence/",
        ],
        "requirements": [{"id": "R1", "text": "PING is ok", "verify": OK_COMMAND, "verify_cmd": OK_COMMAND}],
        "source_refs": [{"id": "S1", "text": "PING is ok", "maps_to": ["R1"]}],
    }
    (task / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (task / "worker-report.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-001",
                "window": "C1",
                "status": "worker_done",
                "covered_requirements": ["R1"],
                "evidence": [{"requirement_id": "R1", "path": ".task/TASK-001/evidence/notes.md", "note": "ok"}],
                "changed_files": ["src/ping.py", ".task/TASK-001/worker-report.json", ".task/TASK-001/evidence/notes.md"],
                "tests": [{"command": OK_COMMAND, "exit_code": 0}],
                "known_gaps": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if status == "verified":
        (task / "verify-report.json").write_text(
            json.dumps(
                {
                    "task_id": "TASK-001",
                    "reviewer": reviewer,
                    "result": "pass",
                    "checked_requirements": ["R1"],
                    "missing": [],
                    "notes": "c2 独立验收",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    round_data = {
        "round_id": "ROUND-001",
        "expected_windows": ["C1", "C2"],
        "receipts": ["C1"],
        "window_status": {"C1": "worker_done", "C2": "pending"},
        "tasks": {route: ["TASK-001"], "C2": []} if route != "C2" else {"C1": [], "C2": ["TASK-001"]},
        "check_requested": True,
        "hook_supervision": False,
    }
    if verifier:
        round_data["verifier_assignments"] = {"TASK-001": verifier}
    (root / ".task" / "round.json").write_text(json.dumps(round_data, indent=2) + "\n", encoding="utf-8")


def manifest_of(root: Path) -> dict:
    return json.loads((root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8"))


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="v034-verifier-"))

    ok_root = temp / "ok"
    build(ok_root, status="verified")
    code, out = run(["--root", str(ok_root), "gate", "TASK-001"])
    expect("V-pos-c1-worker-c2-verifier-passes", code == 0 and "RESULT PASS" in out, out)

    missing_root = temp / "missing"
    build(missing_root, verifier=None, status="verified")
    code, out = run(["--root", str(missing_root), "gate", "TASK-001"])
    expect(
        "V-neg-missing-assignment",
        code != 0 and "VERIFIER_ASSIGNMENT_MISSING" in out,
        out,
    )

    outsider_root = temp / "outsider"
    build(outsider_root, verifier="M9", status="verified", reviewer="M9")
    code, out = run(["--root", str(outsider_root), "gate", "TASK-001"])
    expect(
        "V-neg-verifier-not-in-round",
        code != 0 and "VERIFIER_ASSIGNMENT_CONFLICT" in out,
        out,
    )

    same_root = temp / "same"
    build(same_root, verifier="C1", status="verified", reviewer="C1")
    code, out = run(["--root", str(same_root), "gate", "TASK-001"])
    expect(
        "V-neg-verifier-equals-owner",
        code != 0 and "VERIFIER_ASSIGNMENT_CONFLICT" in out,
        out,
    )

    impostor_root = temp / "impostor"
    build(impostor_root, verifier="C2", reviewer="M9", status="verified")
    code, out = run(["--root", str(impostor_root), "gate", "TASK-001"])
    expect(
        "V-neg-reviewer-not-assigned-verifier",
        code != 0 and "VERIFIER_ASSIGNMENT_CONFLICT" in out and "assigned verifier is C2" in out,
        out,
    )

    misroute_root = temp / "misroute"
    build(misroute_root, route="C2", status="verified")
    code, out = run(["--root", str(misroute_root), "gate", "TASK-001"])
    expect(
        "V-neg-worker-route-conflict",
        code != 0 and "WORKER_ASSIGNMENT_CONFLICT" in out,
        out,
    )

    short_root = temp / "short"
    build(short_root, risk="low", status="verified")
    code, out = run(["--root", str(short_root), "gate", "TASK-001"])
    expect(
        "V-neg-short-path-must-not-assign-verifier",
        code != 0 and "VERIFIER_ASSIGNMENT_CONFLICT" in out and "short close path" in out,
        out,
    )

    assign_root = temp / "assign"
    build(assign_root, verifier=None, status="verified")
    before = manifest_of(assign_root)
    block_before = before.get("block_attempts")
    code, out = run(["--root", str(assign_root), "assign-verifier", "TASK-001", "--window", "C2"])
    after = manifest_of(assign_root)
    round_after = json.loads((assign_root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "V-pos-assign-verifier-writes-route-only",
        code == 0
        and "VERIFIER_ASSIGNED" in out
        and round_after["verifier_assignments"] == {"TASK-001": "C2"}
        and after["owner"] == before["owner"]
        and after["attempt"] == before["attempt"]
        and after.get("block_attempts") == block_before,
        f"code={code} out={out} round={round_after.get('verifier_assignments')}",
    )
    code, out = run(["--root", str(assign_root), "assign-verifier", "TASK-001", "--window", "C1"])
    expect(
        "V-neg-assign-verifier-equals-owner",
        code != 0 and "cannot equal owner" in out,
        out,
    )

    sync_root = temp / "sync"
    build(sync_root, route="C2", verifier="C2", status="verifier_pending")
    code, out = run(["--root", str(sync_root), "sync-worker-route", "TASK-001"])
    round_synced = json.loads((sync_root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "V-pos-sync-worker-route",
        code == 0
        and "C1" in round_synced["tasks"]
        and "TASK-001" in round_synced["tasks"]["C1"]
        and "TASK-001" not in round_synced["tasks"]["C2"]
        and manifest_of(sync_root)["owner"] == "C1",
        f"code={code} out={out} tasks={round_synced.get('tasks')}",
    )
    (sync_root / ".task" / "TASK-001" / "verify-report.json").write_text(
        json.dumps({"task_id": "TASK-001", "reviewer": "C2", "result": "pass",
                    "checked_requirements": ["R1"], "missing": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    code, out = run(["--root", str(sync_root), "sync-worker-route", "TASK-001"])
    expect(
        "V-neg-sync-after-verification-started",
        code != 0 and "verification already started" in out,
        out,
    )

    round_init_root = temp / "round-init"
    (round_init_root / ".task").mkdir(parents=True)
    code, out = run(
        ["--root", str(round_init_root), "round-init", "ROUND-009", "C1", "C2",
         "--task", "C1=TASK-001", "--verifier", "TASK-001=C2"]
    )
    round_data = json.loads((round_init_root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "V-pos-round-init-declares-both-routes",
        code == 0
        and round_data["tasks"] == {"C1": ["TASK-001"], "C2": []}
        and round_data["verifier_assignments"] == {"TASK-001": "C2"},
        f"code={code} out={out} round={json.dumps(round_data)}",
    )
    same_init_root = temp / "round-init-same"
    (same_init_root / ".task").mkdir(parents=True)
    code, out = run(
        ["--root", str(same_init_root), "round-init", "ROUND-010", "C1", "C2",
         "--task", "C1=TASK-001", "--verifier", "TASK-001=C1"]
    )
    expect(
        "V-neg-round-init-verifier-equals-worker",
        code != 0 and "cannot equal the worker window" in out,
        out,
    )

    code, out = run(["--root", str(ok_root), "receipt", "C2", "--role", "verifier"])
    expect(
        "V-pos-verifier-receipt",
        code == 0 and "role=verifier" in out,
        f"code={code} out={out}",
    )
    ok_round = json.loads((ok_root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "V-pos-verifier-receipt-recorded-as-verifier",
        ok_round.get("receipts_by_role", {}).get("C2") == "verifier"
        and ok_round["window_status"]["C2"] == "verified",
        json.dumps(ok_round.get("receipts_by_role")),
    )
    outsider_receipt_root = temp / "receipt-outsider"
    build(outsider_receipt_root, verifier="C2", status="verified")
    code, out = run(["--root", str(outsider_receipt_root), "receipt", "M9", "--role", "verifier"])
    expect(
        "V-neg-verifier-receipt-from-unassigned-window",
        code != 0 and ("not an assigned verifier" in out or "not in expected_windows" in out),
        out,
    )

    code, out = run(["--root", str(ok_root), "audit-round"])
    expect(
        "V-pos-audit-round-with-assignment",
        code == 0 and "ROUND_READY_TO_CLOSE" in out,
        out,
    )

    print("ALL v0.36 VERIFIER ASSIGNMENT CHECKS PASSED")
    print(f"temp: {temp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
