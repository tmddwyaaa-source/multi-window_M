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
    # 必须带 `schema_version`：`init` 已把项目契约升到当前值，手写一份没有版本
    # 标记的 round.json 会被 `SCHEMA_REGRESSION_RISK` 正确拦下。
    (root / ".task" / "round.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
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

    # 第二次失败必须是**新的一轮修复之后**的失败：0.36 起失败身份 = 修复轮次 +
    # 卡点 + 负责人（reason 不参与）。所以真实流程是"原负责人恢复施工 → 再次失败"，
    # 而不是在同一轮里重复提交（那属于同一次失败的补充说明）。
    run(["--root", str(root), "transition", "TASK-001", "in_progress", "--actor", "M1"])
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

    # 第三次失败同样需要"恢复施工 → 再次失败"
    run(["--root", str(root), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B1", "--reason", "f3", "--actor", "worker"]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-third-failure-hits-cap",
        # 0.36：与 cmd_reopen 统一口径 —— 到上限时给出 HARD_STOP token 并非 0 退出，
        # 让调用方/脚本能可靠识别"已硬停"，而不是只看到一句中文提示。
        code != 0
        and manifest["block_attempts"]["attempts"] == 3
        and "硬停" in out
        and "HARD_STOP" in out,
        f"code={code} out={out} block={manifest.get('block_attempts')}",
    )

    # 0.36 语义：硬停之后**施工被拦住**，所以"第四次失败"在真实流程里无法发生。
    # - 同轮再提交 → 只作补充说明（不增加计数）；
    # - 想恢复施工 → 被硬停保护拒绝。
    code, out = run(
        ["--root", str(root), "attempt", "TASK-001", "--block-id", "B1", "--reason", "f4", "--actor", "worker"]
    )
    manifest = manifest_of(root)
    expect(
        "K-neg-fourth-failure-not-counted",
        code == 0
        and "FAILURE_SUPPLEMENTED" in out
        and manifest["block_attempts"]["attempts"] == 3,
        f"code={code} out={out} block={manifest.get('block_attempts')}",
    )
    code, out = run(["--root", str(root), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect(
        "K-neg-hard-stop-blocks-resume",
        code != 0 and "HARD_STOP_ACTIVE" in out,
        f"code={code} out={out}",
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

    # 0.36：第二次失败必须是"恢复施工之后"的失败（失败身份含修复轮次）。
    # 所以先让 C1 重新开工，再由 M1 打回 —— 这才是真实时序。
    run(["--root", str(root), "transition", "TASK-001", "in_progress", "--actor", "M1"])
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

    # 越界窗拒绝用**独立场景**测：这里已经进入"第 2 次失败"状态，
    # 而 0.36 要求硬停/阻塞的判定优先于其它校验，混在这一串里会测错对象。
    outsider_root = Path(tempfile.mkdtemp(prefix="m036-outsider-"))
    seed(outsider_root)
    run(
        ["--root", str(outsider_root), "attempt", "TASK-001",
         "--block-id", "B1", "--reason", "f1", "--actor", "worker"]
    )
    code, out = run(
        ["--root", str(outsider_root), "reassign", "TASK-001", "--owner", "M9",
         "--reason", "换独立负责人"]
    )
    expect(
        "K-neg-reassign-outsider-rejected",
        code != 0 and "REASSIGN_FAIL" in out and "expected_windows" in out,
        f"code={code} out={out}",
    )
    manifest = manifest_of(root)
    # 方向文件 §6.1：同负责人**可以确认已有交接**（幂等），但不新增记录、不增加失败次数。
    failures_before = manifest_of(root)["block_attempts"]["attempts"]
    history_before = len(manifest_of(root).get("assignment_history") or [])
    code, out = run(
        ["--root", str(root), "reassign", "TASK-001", "--owner", "C1", "--reason", "确认已有交接"]
    )
    manifest = manifest_of(root)
    expect(
        "K-pos-reassign-same-owner-confirms-existing-handoff",
        code == 0
        and "HANDOFF_CONFIRMED" in out
        and manifest["owner"] == "C1"
        and manifest["block_attempts"]["attempts"] == failures_before
        and len(manifest.get("assignment_history") or []) == history_before,
        f"code={code} out={out} failures={manifest['block_attempts']['attempts']}",
    )

    # 但"当前负责人从未接手过"（无任何交接历史）时不能拿同 owner 当确认。
    bogus_root = Path(tempfile.mkdtemp(prefix="m036-bogus-"))
    seed(bogus_root)
    current_owner = str(manifest_of(bogus_root).get("owner") or "")
    code, out = run(
        ["--root", str(bogus_root), "reassign", "TASK-001", "--owner", current_owner, "--reason", "假装确认"]
    )
    expect(
        "K-neg-reassign-same-owner-without-history-rejected",
        code != 0 and "无法当作确认" in out,
        f"code={code} out={out} owner={current_owner}",
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
