"""v0.33: 卡点三级阶梯、根因绑定（改名不算换根因）、负责人交接历史。

跨宿主稳定性规则（三方对齐）：
  第 1 次 fail → 复用原执行者（并复述打回项）
  第 2 次 fail → 换真正不同的负责窗口/独立执行上下文，并更新 owner
  第 3 次 fail → 硬停，写 BLOCKERS；宿主无合格替换者时写 NO_ELIGIBLE_REPLACEMENT_WORKER
计数必须绑定 block_id：同一根因不能靠改名重置；换人不能只在聊天里说。
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


def seed(root: Path) -> None:
    run(["--root", str(root), "init", "TASK-001", "--owner", "M2", "--title", "卡点测试", "--risk", "low"])
    path = root / ".task" / "TASK-001" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["allowed_paths"] = ["src/"]
    manifest["source_refs"] = [{"id": "S1", "text": "t", "maps_to": ["R1"]}]
    manifest["requirements"] = [{"id": "R1", "text": "x", "verify": "y"}]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # `reassign` 要求新负责人在本轮名单内，所以测试项目必须有 round.json。
    (root / ".task" / "round.json").write_text(
        json.dumps(
            {
                "round_id": "ROUND-001",
                "expected_windows": ["M2", "C1", "M5"],
                "receipts": [],
                "window_status": {"M2": "worker_done", "C1": "pending", "M5": "pending"},
                "tasks": {"M2": ["TASK-001"], "C1": [], "M5": []},
                "check_requested": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def manifest_of(root: Path) -> dict:
    return json.loads((root / ".task" / "TASK-001" / "manifest.json").read_text(encoding="utf-8"))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m033-block-"))
    seed(root)

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B1", "--reason", "f1", "--actor", "worker"]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-first-failure-counted",
        code == 0
        and manifest["block_attempts"]["block_id"] == "B1"
        and manifest["block_attempts"]["attempts"] == 1
        and manifest["attempt"] == 1
        and manifest["owner"] == "M2",
        f"code={code} out={out} block={manifest.get('block_attempts')}",
    )
    expect(
        "K-pos-no-handoff-without-owner",
        not manifest.get("assignment_history"),
        json.dumps(manifest.get("assignment_history")),
    )

    code, out = run(
        [
            "--root", str(root), "attempt", "TASK-001",
            "--block-id", "B1", "--reason", "f2", "--actor", "worker", "--owner", "C1",
        ]
    )
    manifest = manifest_of(root)
    handoffs = manifest.get("assignment_history") or []
    expect(
        "K-pos-second-failure-changes-owner",
        code == 0
        and manifest["owner"] == "C1"
        and manifest["block_attempts"]["attempts"] == 2
        and len(handoffs) == 1
        and handoffs[0]["from"] == "M2"
        and handoffs[0]["to"] == "C1"
        and handoffs[0]["failure_count"] == 2
        and "REASSIGN_REQUIRED" in out
        and manifest["reassign_required"] is True
        and manifest["status"] == "blocked"
        and (root / "docs" / "BLOCKERS" / "TASK-001-B1.md").exists(),
        f"code={code} out={out} owner={manifest.get('owner')} handoffs={handoffs}",
    )
    expect(
        "K-pos-block-history-alias",
        isinstance(manifest.get("block_history"), list)
        and manifest["block_history"] == manifest["block_attempts"]["history"],
        json.dumps(manifest.get("block_history")),
    )

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B1", "--reason", "f3", "--actor", "worker"]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-third-failure-hits-cap",
        code == 0 and manifest["block_attempts"]["attempts"] == 3 and "硬停" in out,
        f"code={code} out={out} block={manifest.get('block_attempts')}",
    )

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B1", "--reason", "f4", "--actor", "worker"]
    )
    manifest = manifest_of(root)
    expect(
        "K-neg-fourth-failure-rejected",
        code != 0
        and "卡点上限" in out
        and manifest["block_attempts"]["attempts"] == 3,
        f"code={code} out={out} block={manifest.get('block_attempts')}",
    )

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B2", "--reason", "改名", "--actor", "worker"]
    )
    expect(
        "K-neg-rename-does-not-reset",
        code != 0 and "BLOCK_ID_CHANGE_REQUIRES_EVIDENCE" in out,
        f"code={code} out={out}",
    )

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B2", "--new-root", "--actor", "worker"]
    )
    expect(
        "K-neg-new-root-needs-evidence",
        code != 0 and "requires an evidence-bearing" in out,
        f"code={code} out={out}",
    )

    code, out = run(
        [
            "--root", str(root), "attempt", "TASK-001",
            "--block-id", "B2", "--new-root", "--reason", "新证据：需求变更", "--actor", "worker",
        ]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-new-root-resets-block-only",
        code == 0
        and manifest["block_attempts"]["block_id"] == "B2"
        and manifest["block_attempts"]["attempts"] == 1
        and manifest["attempt"] == 4
        and manifest["owner"] == "C1",
        f"code={code} out={out} manifest={json.dumps(manifest.get('block_attempts'))}",
    )
    expect(
        "K-pos-handoff-history-survives-new-root",
        len(manifest.get("assignment_history") or []) == 1
        and (manifest["assignment_history"][0]["block_id"]) == "B1",
        json.dumps(manifest.get("assignment_history")),
    )

    policy = manifest
    expect(
        "K-pos-policy-uses-task-attempt-only",
        policy["attempt"] == 4,
        f"task attempt must stay monotone, got {policy.get('attempt')}",
    )

    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B2", "--actor", "verifier", "--role", "worker"]
    )
    expect(
        "K-neg-role-actor-mismatch",
        code != 0 and "requires actor" in out,
        f"code={code} out={out}",
    )

    code, out = run(
        ["--root", str(root), "reopen", "TASK-001", "--block-id", "B2", "--reason", "M1 打回"]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-reopen-counts-block-and-demands-reassign",
        code != 0
        and "REASSIGN_REQUIRED" in out
        and manifest["status"] == "blocked"
        and manifest["reassign_required"] is True
        and manifest["block_attempts"]["attempts"] == 2
        and manifest["attempt"] == 5,
        f"code={code} out={out} block={manifest.get('block_attempts')} attempt={manifest.get('attempt')}",
    )

    code, out = run(
        ["--root", str(root), "reassign", "TASK-001", "--owner", "M9", "--reason", "换独立负责人"]
    )
    manifest = manifest_of(root)
    expect(
        "K-neg-reassign-outsider-rejected",
        code != 0 and "REASSIGN_FAIL" in out and "expected_windows" in out,
        f"code={code} out={out}",
    )
    code, out = run(
        ["--root", str(root), "reassign", "TASK-001", "--owner", "C1", "--reason", "同一个人不算换"]
    )
    expect(
        "K-neg-reassign-same-owner-rejected",
        code != 0 and "must differ from current owner" in out,
        f"code={code} out={out}",
    )

    tasks_before = json.loads((root / ".task" / "round.json").read_text(encoding="utf-8"))["tasks"]
    code, out = run(
        ["--root", str(root), "reassign", "TASK-001", "--owner", "M5", "--reason", "换独立负责人"]
    )
    manifest = manifest_of(root)
    round_after = json.loads((root / ".task" / "round.json").read_text(encoding="utf-8"))
    expect(
        "K-pos-reassign-clears-flag-and-moves-route",
        code == 0
        and "REASSIGNED" in out
        and manifest["owner"] == "M5"
        and manifest["reassign_required"] is False
        and manifest["status"] == "in_progress"
        and "TASK-001" in round_after["tasks"].get("M5", [])
        and all("TASK-001" not in v for k, v in round_after["tasks"].items() if k != "M5"),
        f"code={code} out={out} owner={manifest.get('owner')} tasks={round_after.get('tasks')} was={tasks_before}",
    )

    code, out = run(["--root", str(root), "brief", "TASK-001", "--role", "worker"])
    expect(
        "K-pos-brief-shows-block-budget",
        code == 0 and "卡点" in out and "B2" in out,
        f"code={code} out={out}",
    )

    code, out = run(["--root", str(root), "status", "--markdown"])
    expect(
        "K-pos-status-shows-block-column",
        code == 0 and "卡点" in out and "B2 2/3" in out,
        f"code={code} out={out}",
    )

    print("ALL v0.33 BLOCK STAIRCASE CHECKS PASSED")
    print(f"temp: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
