"""v0.36 验收：按《0.36 方向文件》第 7 节逐项验证。

覆盖：
  - 字面要求：字段拼错定位、第二次真换人不重复计数、第三次硬停、验收者仍独立、
    合法旧项目迁移后可操作且失败不半迁移、流程产物变化不再卡收口、
    派工新脚本允许不存在而收口缺脚本会失败
  - 新增合同/路径行为：合法标准调整与不明调整、未申报越界、其他任务合法改动、
    用户既有改动、共享文件归属不明
  - 另加：文档注释里的 `gate --full` 必须不存在（文档与实现一致）
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
SKILL_ROOT = HERE.parent


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(TASKCTL), "--root", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "-m", "baseline"],
        check=True,
        capture_output=True,
    )


def new_root(tag: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m036-{tag}-"))
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (root / ".gitignore").write_text(".task/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    git_init(root)
    return root


def seed_task(
    root: Path,
    task_id: str = "TASK-001",
    *,
    owner: str = "M2",
    status: str = "done",
    risk: str = "low",
    commands: list[str] | None = None,
    allowed: list[str] | None = None,
) -> Path:
    manifest_path = root / ".task" / task_id / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "task_id": task_id,
            "title": "验收样本",
            "owner": owner,
            "track": "feature",
            "risk": risk,
            "attempt": 0,
            "status": status,
            "status_history": [{"status": status, "at": "2026-01-01T00:00:00+00:00", "by": "M1"}],
            "allowed_paths": allowed if allowed is not None else ["src/"],
            "source_refs": [{"id": "S1", "text": "需求", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "可验收需求", "verify": "人工检查"}],
        },
    )
    evidence_dir = root / ".task" / task_id / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "R1.txt").write_text("verified\n", encoding="utf-8")
    write_json(
        root / ".task" / task_id / "worker-report.json",
        {
            "task_id": task_id,
            "window": owner,
            "status": "worker_done",
            "covered_requirements": ["R1"],
            "evidence": [
                {"requirement_id": "R1", "path": f".task/{task_id}/evidence/R1.txt", "note": "已验证"}
            ],
            "changed_files": [],
            "known_gaps": [],
            "tests": [{"command": c} for c in (commands or [])],
        },
    )
    return manifest_path


def seed_round(root: Path, *, windows=None, tasks=None, receipts=None) -> Path:
    windows = windows or ["M2", "C1"]
    receipts = receipts or []
    # 注意：不能省略（此前有 fixture 因缺它被正确判成负例）
    write_json(
        root / ".task" / "round.json",
        {
            "schema_version": 1,
            "round_id": "ROUND-001",
            "expected_windows": windows,
            "receipts": receipts,
            "window_status": {w: ("worker_done" if w in receipts else "pending") for w in windows},
            "tasks": tasks if tasks is not None else {"M2": ["TASK-001"], "C1": []},
            "verifier_assignments": {},
            "gears": {"capability": "default", "collaboration": "P"},
            "hook_supervision": False,
            "check_requested": False,
        },
    )
    return root / ".task" / "round.json"


def main() -> int:
    # ---------- 1. 字段拼错要有定位提示 ----------
    r = new_root("fields")
    write_json(
        r / ".task" / "TASK-001" / "manifest.json",
        {
            "schema_version": 1, "task_id": "TASK-001", "owner": "M2", "track": "feature",
            "risk": "low", "attempt": 0, "status": "done",
            "status_history": [{"status": "done", "at": "2026-01-01T00:00:00+00:00", "by": "M1"}],
            "allowed_paths": ["src/"],
            "source_refs": [{"id": "S1", "text": "t", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "x", "verify": "人工检查"}],
        },
    )
    ev = r / ".task" / "TASK-001" / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "R1.txt").write_text("ok\n", encoding="utf-8")
    write_json(
        r / ".task" / "TASK-001" / "worker-report.json",
        {
            "task_id": "TASK-001", "window": "M2", "status": "worker_done",
            "covered_requirements": ["R1"],
            "evidence": [{"requirement_id": "R1", "path": ".task/TASK-001/evidence/R1.txt"}],
            "changed_files": [], "known_gaps": [],
            # 工人写成了 cmd —— 必须报错并指出正确字段名，而不是静默漏跑
            "tests": [{"cmd": "node -e \"process.exit(0)\""}],
        },
    )
    seed_round(r)
    code, out = run(["gate", "TASK-001"], r)
    expect(
        "F-neg-misspelled-command-field-is-located",
        code == 1
        and "UNKNOWN_FIELD" in out
        and "cmd" in out
        and "command" in out
        and "tests[1]" in out,
        f"code={code} out={out}",
    )

    # ---------- 2. 第二次真换人：不重复计数、保留第三次硬停 ----------
    s = new_root("staircase")
    write_json(
        s / ".task" / "TASK-001" / "manifest.json",
        {
            "schema_version": 1, "task_id": "TASK-001", "title": "t", "owner": "M2",
            "track": "feature", "risk": "low", "attempt": 0, "status": "in_progress",
            "status_history": [{"status": "in_progress", "at": "2026-01-01T00:00:00+00:00", "by": "M1"}],
            "allowed_paths": ["src/"],
            "source_refs": [{"id": "S1", "text": "t", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "x", "verify": "y"}],
        },
    )
    seed_round(s, windows=["M2", "M4", "M5"], tasks={"M2": ["TASK-001"], "M4": [], "M5": []})

    def failures() -> int:
        return read_json(s / ".task" / "TASK-001" / "manifest.json")["block_attempts"]["attempts"]

    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "f1", "--actor", "worker"], s)
    before = failures()
    # 同一轮内再次提交 = 同一次失败的**补充说明**：记录证据但不增加计数
    code, out = run(
        ["attempt", "TASK-001", "--block-id", "B1", "--reason", "补充：现象仍在", "--actor", "worker"], s
    )
    expect(
        "F-pos-duplicate-failure-not-counted",
        code == 0 and failures() == before and "FAILURE_SUPPLEMENTED" in out,
        f"code={code} failures={failures()} before={before} out={out}",
    )
    # 第二次真实失败必须是"恢复施工之后"的失败（失败身份含修复轮次）
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], s)
    code, out = run(
        ["attempt", "TASK-001", "--block-id", "B1", "--reason", "f2", "--actor", "worker"], s
    )
    expect(
        "F-pos-second-failure-demands-reassign",
        "REASSIGN_REQUIRED" in out and failures() == 2,
        f"code={code} failures={failures()} out={out}",
    )
    code, out = run(["reassign", "TASK-001", "--owner", "M4", "--reason", "换人"], s)
    expect(
        "F-pos-reassign-does-not-add-failures",
        code == 0 and failures() == 2 and "REASSIGNED" in out,
        f"code={code} failures={failures()} out={out}",
    )
    # 重复同一交接 → 只确认
    code, out = run(["reassign", "TASK-001", "--owner", "M4", "--reason", "换人"], s)
    expect(
        "F-pos-duplicate-handoff-confirmed-not-recorded",
        code == 0 and "HANDOFF_CONFIRMED" in out and failures() == 2,
        f"code={code} out={out}",
    )
    # 第三次真实失败（同样要"恢复施工之后"）→ 硬停仍然保留
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], s)
    code, out = run(
        ["attempt", "TASK-001", "--block-id", "B1", "--reason", "f3", "--actor", "worker"], s
    )
    manifest = read_json(s / ".task" / "TASK-001" / "manifest.json")
    expect(
        "F-pos-third-failure-still-hard-stops",
        failures() == 3 and manifest.get("hard_stop") is True and "HARD_STOP" in out,
        f"failures={failures()} hard_stop={manifest.get('hard_stop')} out={out}",
    )
    # 硬停后普通路径不得恢复施工（rev2 Q2 的硬停保护）
    code, out = run(["reassign", "TASK-001", "--owner", "M5", "--reason", "普通换人"], s)
    expect(
        "F-neg-hard-stop-blocks-plain-reassign",
        code != 0 and "HARD_STOP_ACTIVE" in out,
        f"code={code} out={out}",
    )

    # ---------- 3. 验收者仍独立 ----------
    v = new_root("verifier")
    seed_task(v, status="done", risk="medium", owner="M2")
    seed_round(
        v,
        windows=["M2", "C1"],
        tasks={"M2": ["TASK-001"], "C1": []},
    )
    code, out = run(["gate", "TASK-001"], v)
    expect(
        "F-pos-medium-risk-still-needs-verifier",
        code == 1 and "VERIFIER_ASSIGNMENT_MISSING" in out,
        f"code={code} out={out}",
    )

    # ---------- 4. 合同基线：标准变更提示、不明变更要确认后才收口 ----------
    c = new_root("contract")
    seed_task(c, status="in_progress", owner="M2", commands=["node -e \"process.exit(0)\""])
    seed_round(c, receipts=["M2", "C1"])
    code, out = run(["brief", "TASK-001", "--role", "worker"], c)
    expect("F-pos-brief-creates-contract-baseline", (c / ".task" / "TASK-001" / "contract.json").is_file(), out)
    baseline = read_json(c / ".task" / "TASK-001" / "contract.json")
    expect(
        "F-pos-baseline-holds-verify-and-paths",
        baseline["baseline"]["allowed_paths"] == ["src/"]
        and baseline["baseline"]["requirements"][0]["verify_cmd"] == ""
        and baseline["baseline"]["risk"] == "low",
        json.dumps(baseline, ensure_ascii=False)[:300],
    )
    # 合法标准调整：M1 明确改 allowed_paths
    manifest_path = c / ".task" / "TASK-001" / "manifest.json"
    data = read_json(manifest_path)
    data["allowed_paths"] = ["src/", "tests/"]
    data["status"] = "done"
    write_json(manifest_path, data)
    code, out = run(["gate", "TASK-001"], c)
    expect(
        "F-neg-acceptance-change-is-surfaced-not-silent",
        code == 1 and "ACCEPTANCE_CHANGED" in out and "tests/" in out,
        f"code={code} out={out}",
    )
    expect(
        "F-pos-change-does-not-claim-relaxation",
        "不自动等于放宽" in out and "原需求" in out,
        f"code={code} out={out}",
    )

    # ---------- 5. 路径检查：整轮越界 vs 其他任务合法改动 ----------
    o = new_root("overrun")
    seed_task(o, task_id="TASK-001", status="done", owner="M2", allowed=["src/"])
    seed_task(o, task_id="TASK-002", status="done", owner="M4", allowed=["lib/"])
    # 任务内申报检查仍在：lib/b.js 属于 TASK-002 的 allowed_paths，必须申报
    t2_report = o / ".task" / "TASK-002" / "worker-report.json"
    t2_data = read_json(t2_report)
    t2_data["changed_files"] = ["lib/b.js"]
    write_json(t2_report, t2_data)
    (o / "lib").mkdir(exist_ok=True)
    (o / "lib" / "b.js").write_text("console.log(2);\n", encoding="utf-8")
    # 另一位任务的合法改动（不在本任务 allowed_paths，但属于整轮授权范围）
    (o / "lib" / "b.js").write_text("console.log(3);\n", encoding="utf-8")
    seed_round(
        o,
        windows=["M2", "M4"],
        tasks={"M2": ["TASK-001"], "M4": ["TASK-002"]},
        receipts=["M2", "M4"],
    )
    code, out = run(["audit-round"], o)
    expect(
        "F-pos-other-task-legit-change-not-flagged",
        "UNDECLARED_CHANGE" not in out,
        f"code={code} out={out}",
    )
    # 整轮都无法解释的改动 → 阻断
    (o / "outside").mkdir(exist_ok=True)
    (o / "outside" / "rogue.js").write_text("console.log(9);\n", encoding="utf-8")
    run(["request-check"], o)  # 真实流程：先请求审计，audit-round 才会真正跑
    code, out = run(["audit-round"], o)
    expect(
        "F-neg-unexplained-round-overrun-blocks-close",
        code == 1 and "UNDECLARED_CHANGE" in out and "rogue.js" in out,
        f"code={code} out={out}",
    )

    # ---------- 6. WEAK_ACCEPTANCE 仅提示，不影响判定 ----------
    w = new_root("weak")
    seed_task(w, status="pending", owner="M2", commands=["node --check src/app.js"])
    seed_round(w)
    code, out = run(["packet", "TASK-001", "--role", "worker"], w)
    expect(
        "F-pos-weak-acceptance-is-advisory",
        code == 0 and "WEAK_ACCEPTANCE" in out and "不是判定" in out,
        f"code={code} out={out[:400]}",
    )
    code, out = run(["gate", "TASK-001", "--basic"], w)
    expect(
        "F-pos-weak-acceptance-does-not-fail-gate",
        "WEAK_ACCEPTANCE" not in out,
        f"code={code} out={out}",
    )

    # ---------- 7. 文档与实现一致：不再教 gate --full ----------
    # 只扫"会被 M1 当规则读"的文件；测试文件自己要引用这个坏字符串，不回扫自身。
    # CHANGELOG 允许**记载**这个历史缺陷；标记 `0.36-doc-note:` 的行豁免
    # （它是在解释"为什么改"，不是在承诺参数存在）。
    offenders: list[str] = []
    targets = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "templates.md", SKILL_ROOT / "CHANGELOG.md"]
    targets += sorted((SKILL_ROOT / "references").glob("*.md"))
    targets.append(TASKCTL)
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "gate --full" not in line:
                continue
            if "0.36-doc-note:" in line:
                continue  # 显式豁免：这一行是在记载历史缺陷，不是教用法
            offenders.append(f"{path.name}:{lineno}")
    expect(
        "F-neg-no-doc-teaches-nonexistent-gate-full",
        not offenders,
        f"offenders={offenders}",
    )
    # 并且实现层也不该接受这个参数（文档写了却不支持 = 照做即错）
    code, out = run(["gate", "TASK-001", "--full"], new_root("gflag"))
    expect(
        "F-neg-gate-full-is-really-not-a-flag",
        code != 0 and "unrecognized arguments" in out,
        f"code={code} out={out[:200]}",
    )

    print("ALL v0.36 DIRECTION-FILE ACCEPTANCE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
