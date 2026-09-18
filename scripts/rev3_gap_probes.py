"""独立复现 Codex rev3 复核提出的 4 条 + 迁移回滚缺口。

不照抄它的脚本；每条都构造真实场景并检查前后文件状态。
退出码反映结果（有 FAIL 即非 0）。
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
sys.path.insert(0, str(HERE))
import taskctl as t  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(name)
        print(f"      {detail}")


def run(args, cwd):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    c = subprocess.run(
        [sys.executable, str(TASKCTL), "--root", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return c.returncode, (c.stdout or "") + (c.stderr or "")


def wj(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rj(p):
    return json.loads(p.read_text(encoding="utf-8"))


def task(status="pending", allowed=None, verify='node -e "process.exit(3)"'):
    return {
        "schema_version": 1, "task_id": "TASK-001", "title": "t", "owner": "M2",
        "track": "f", "risk": "low", "attempt": 0, "status": status,
        "status_history": [{"status": status, "at": "2026-01-01T00:00:00+00:00", "by": "taskctl"}],
        "allowed_paths": allowed if allowed is not None else ["src/"],
        "source_refs": [{"id": "S1", "text": "原需求原文", "maps_to": ["R1"]}],
        "requirements": [{"id": "R1", "text": "要能跑", "verify": verify}],
    }


def setup(tag, verify='node -e "process.exit(3)"'):
    root = Path(tempfile.mkdtemp(prefix=f"rv3-{tag}-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    wj(root / ".task" / "TASK-001" / "manifest.json", task(verify=verify))
    wj(root / ".task" / "round.json", {
        "schema_version": 1, "round_id": "R1", "expected_windows": ["M2", "C1"],
        "receipts": [], "window_status": {"M2": "pending", "C1": "pending"},
        "tasks": {"M2": ["TASK-001"], "C1": []}, "verifier_assignments": {},
        "gears": {"capability": "default", "collaboration": "P"},
        "hook_supervision": False, "check_requested": False,
    })
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    run(["gate", "TASK-001", "--dispatch"], root)
    return root


# ---------- 1. 丢失基线可被 brief 重新生成（覆盖原标准） ----------
def m1_lost_baseline_rebuilt_by_brief():
    root = setup("lost")
    cpath = root / ".task" / "TASK-001" / "contract.json"
    check("M1a 派工点已建基线", cpath.is_file(), f"exists={cpath.is_file()}")
    baseline_before = rj(cpath)["baseline"]["requirements"][0]["verify"]
    m = root / ".task" / "TASK-001" / "manifest.json"
    d = rj(m)
    d["requirements"][0]["verify"] = 'node -e "process.exit(0)"'  # 放宽原标准
    wj(m, d)
    cpath.unlink()  # 基线丢失
    code, out = run(["brief", "TASK-001", "--role", "worker"], root)
    rebuilt = cpath.is_file()
    if rebuilt:
        after = rj(cpath)["baseline"]["requirements"][0]["verify"]
    else:
        after = None
    check(
        "M1 丢失基线不得被 brief 用当前标准重建",
        not (rebuilt and after == 'node -e "process.exit(0)"'),
        f"brief_code={code} rebuilt={rebuilt} new_baseline_verify={after!r} 原标准={baseline_before!r}",
    )


# ---------- 2. mtime 兜底 + 归属/身份错误仍被接受 ----------
def m2_accept_evidence_too_weak():
    root = setup("evidence")
    m = root / ".task" / "TASK-001" / "manifest.json"
    d = rj(m)
    d["requirements"][0]["verify"] = 'node -e "process.exit(0)"'
    wj(m, d)
    # (a) 只有 mtime 更新、没有 contract_key 的泛泛 pass 报告
    vr = root / ".task" / "TASK-001" / "verify-report.json"
    wj(vr, {"task_id": "TASK-001", "reviewer": "C1", "result": "pass",
            "checked_requirements": ["R1"], "missing": []})
    code_a, out_a = run(["adjudicate", "TASK-001", "accept", "--reason", "generic pass"], root)
    # (b) 归属与身份都错误的报告，但 contract_key 匹配
    diffs, _ = t.contract_diffs(root, "TASK-001", d)
    key = t.acceptance_key(diffs)
    wj(vr, {"task_id": "TASK-999", "reviewer": "NOT_A_WINDOW", "result": "fail",
            "checked_requirements": ["R1"], "missing": [], "contract_key": key})
    code_b, out_b = run(["adjudicate", "TASK-001", "accept", "--reason", "bad ownership"], root)
    check("M2a mtime 兜底不得作为接受依据", code_a != 0, f"code={code_a} out={out_a[:160]}")
    check("M2b 归属/身份错误的报告不得被接受", code_b != 0, f"code={code_b} out={out_b[:160]}")


# ---------- 3. 轮次判据忽略已记录的新轮次 ----------
def m3_cycle_ignores_recorded_round():
    m = {
        "status": "reopened",
        "repair_cycle": 2,
        "block_attempts": {
            "block_id": "B1",
            "attempts": 1,
            "history": [{
                "attempt": 1, "block_id": "B1", "failure_id": "x",
                "repair_cycle": 1, "status_after": "reopened", "owner": "M2",
            }],
        },
    }
    got = t.effective_failure_cycle(m, m["block_attempts"])
    check(
        "M3 轮次判据不得忽略已记录的新轮次",
        got >= 2,
        f"manifest.repair_cycle=2 但 effective_failure_cycle={got}（应 >= 2）",
    )


# ---------- 4. 迁移回滚：注入提交期故障，检查是否真的全量恢复 ----------
class _MigrateArgs:
    check = False
    destination = "scripts/taskctl.py"
    force = True


def m4_migration_rollback_gaps():
    """直接在进程内注入一次"提交期写失败"，检查所有被改写的文件是否恢复。

    Codex rev3 指出：回滚清单缺 `skill-lock.json`；且 `b''` 被当成"原来不存在"。
    """
    root = Path(tempfile.mkdtemp(prefix="rv3-rb-"))
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@e.i", "-c", "user.name=t",
                    "commit", "-q", "-m", "b"], check=True, capture_output=True)
    # 合法**旧**任务（无版本标记 → 预检通过且需要转换）+ **原本存在但是空**的目标脚本。
    # 空的既有文件正是 Codex 指出的 b'' 语义问题：回滚时不能把它当成"原来不存在"。
    legacy = task(status="pending")
    legacy.pop("schema_version", None)
    wj(root / ".task" / "TASK-001" / "manifest.json", legacy)
    (root / "scripts").mkdir()
    empty_script = root / "scripts" / "taskctl.py"
    empty_script.write_text("", encoding="utf-8")

    original_write = t.write_json
    state = {"raised": False}

    def flaky_write(path, value):
        name = Path(path).name
        # 让"写 skill-lock.json"这一步失败，模拟提交期 I/O 故障
        if name == "skill-lock.json" and not state["raised"]:
            state["raised"] = True
            raise OSError("injected failure while writing skill-lock.json")
        return original_write(path, value)

    t.write_json = flaky_write
    try:
        code = t.cmd_migrate_project(_MigrateArgs(), root)
    except SystemExit:
        code = 1
    finally:
        t.write_json = original_write

    script_text = empty_script.read_text(encoding="utf-8") if empty_script.exists() else None
    lock_file = root / ".task" / "skill-lock.json"
    lock = rj(lock_file) if lock_file.is_file() else {}

    check("M4a 提交期故障会如实失败", code != 0, f"code={code}")
    check(
        "M4b 空的既有文件不得因回滚而消失",
        script_text == "",
        f"exists={empty_script.exists()} text={script_text!r}",
    )
    check(
        "M4c 版本锁不得残留新值",
        lock.get("schema_version") in (None, 0) and lock.get("status") != "converted",
        f"lock={json.dumps(lock, ensure_ascii=False)[:200]}",
    )
    check(
        "M4d 共同文件未被部分改写",
        rj(root / ".task" / "TASK-001" / "manifest.json").get("schema_version") is None,
        json.dumps(rj(root / ".task" / "TASK-001" / "manifest.json"), ensure_ascii=False)[:120],
    )


def main() -> int:
    print("=== 独立复现 Codex rev3 复核提出的缺口 ===\n")
    m1_lost_baseline_rebuilt_by_brief()
    m2_accept_evidence_too_weak()
    m3_cycle_ignores_recorded_round()
    m4_migration_rollback_gaps()
    print()
    if FAILURES:
        print(f"== {len(FAILURES)} 条已复现：{'; '.join(FAILURES)} ==")
        return 1
    print("== 全部未复现 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
