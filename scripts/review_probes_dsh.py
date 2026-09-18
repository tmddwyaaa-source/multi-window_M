"""独立复验 Codex 审阅结论的 7 条 —— 每条都同时验证**反例不再复现**与**合法路径仍通**。

原则：
  - 不修改原有测试的断言来"让它过"；
  - 每条都构造真实场景，检查前后文件状态与行为，而不是只看 token。
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


def run(args, cwd):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    c = subprocess.run(
        [sys.executable, str(TASKCTL), "--root", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return c.returncode, (c.stdout or "") + (c.stderr or "")


def wj(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rj(path):
    return json.loads(path.read_text(encoding="utf-8"))


FAILURES: list = []


def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(name)
        print(f"      {detail}")


def full_task(owner="M2", allowed=None, verify='node -e "process.exit(3)"', include_text=True,
              schema_version=1):
    """夹具：`init` 已把项目契约升到 1，所以任务文件也必须带同样的标记，
    否则会被 `SCHEMA_REGRESSION_RISK` 正确判成"项目内不一致"。
    迁移类探针传 `schema_version=None` 来模拟旧文件。"""
    req = {"id": "R1", "text": "要能跑", "verify": verify}
    if not include_text:
        req.pop("text")
    task = {
        "task_id": "TASK-001", "title": "t", "owner": owner,
        "track": "f", "risk": "low", "attempt": 0, "status": "pending",
        "status_history": [{"status": "pending", "at": "2026-01-01T00:00:00+00:00", "by": "taskctl"}],
        "allowed_paths": allowed if allowed is not None else ["src/"],
        "source_refs": [{"id": "S1", "text": "用户需求原文", "maps_to": ["R1"]}],
        "requirements": [req],
    }
    if schema_version is not None:
        task["schema_version"] = schema_version
    return task


def git_init(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@e.i", "-c", "user.name=t",
                    "commit", "-q", "-m", "b"], check=True, capture_output=True)


def P1():
    """失败事件识别：同轮重复提交=同一次；新一轮同样错误=新失败。"""
    root = Path(tempfile.mkdtemp(prefix="v1-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    m = root / ".task" / "TASK-001" / "manifest.json"
    wj(m, full_task())
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "animation frozen", "--actor", "worker"], root)
    f1 = rj(m)["block_attempts"]["attempts"]
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "animation frozen", "--actor", "worker"], root)
    f2 = rj(m)["block_attempts"]["attempts"]
    # 修复一轮（换人 → in_progress）后同样错误
    wj(root / ".task" / "round.json", {"schema_version": 1, "round_id": "R1",
       "expected_windows": ["M2", "M4"], "receipts": [],
       "window_status": {"M2": "pending", "M4": "pending"},
       "tasks": {"M2": ["TASK-001"], "M4": []}, "verifier_assignments": {},
       "gears": {"capability": "default", "collaboration": "P"},
       "hook_supervision": False, "check_requested": False})
    run(["reassign", "TASK-001", "--owner", "M4", "--reason", "换人"], root)
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "animation frozen", "--actor", "worker"], root)
    f3 = rj(m)["block_attempts"]["attempts"]
    check("P1a 同轮重复提交不重复计数", f2 == f1, f"f1={f1} f2={f2}")
    check("P1b 新一轮同样错误计数", f3 == f2 + 1, f"f2={f2} f3={f3}")


def P2():
    """硬停保护：普通路径不得恢复施工；显式裁决才放行。"""
    root = Path(tempfile.mkdtemp(prefix="v2-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    m = root / ".task" / "TASK-001" / "manifest.json"
    wj(m, full_task())
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    wj(root / ".task" / "round.json", {"schema_version": 1, "round_id": "R1",
       "expected_windows": ["M2", "M4", "M5"], "receipts": [],
       "window_status": {"M2": "pending", "M4": "pending", "M5": "pending"},
       "tasks": {"M2": ["TASK-001"], "M4": [], "M5": []}, "verifier_assignments": {},
       "gears": {"capability": "default", "collaboration": "P"},
       "hook_supervision": False, "check_requested": False})
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "f1", "--actor", "worker"], root)
    # 0.36：第二次失败必须是"恢复施工之后"的失败（失败身份含修复轮次）
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "f2", "--actor", "worker"], root)
    run(["reassign", "TASK-001", "--owner", "M4", "--reason", "换人"], root)
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "f3", "--actor", "worker"], root)
    check("P2a 第三次仍硬停", rj(m).get("hard_stop") is True, str(rj(m).get("hard_stop")))
    code, out = run(["reassign", "TASK-001", "--owner", "M5", "--reason", "普通换人"], root)
    check("P2b 普通 reassign 被拒", code == 1 and "HARD_STOP_ACTIVE" in out,
          f"code={code} status={rj(m).get('status')}")
    check("P2c 状态未被恢复", rj(m).get("status") == "blocked", str(rj(m).get("status")))
    code, out = run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    check("P2d 普通 transition 被拒", code == 1 and "HARD_STOP_ACTIVE" in out, f"code={code}")
    # 合法出口：显式裁决
    code, out = run(["reassign", "TASK-001", "--owner", "M5", "--reason", "已确认真实根因并补充证据",
                     "--adjudicate-hard-stop"], root)
    after = rj(m)
    check("P2e 显式裁决可放行且留痕",
          code == 0 and after.get("hard_stop") is False
          and (after.get("hard_stop_adjudications") or []),
          f"code={code} hard_stop={after.get('hard_stop')} adj={after.get('hard_stop_adjudications')}")


def P3():
    """合同基线：init 不冻结；需求就绪后自动建立；正常填写不算变更。"""
    root = Path(tempfile.mkdtemp(prefix="v3-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    cpath = root / ".task" / "TASK-001" / "contract.json"
    check("P3a init 不建基线", not cpath.is_file(), f"exists={cpath.is_file()}")
    m = root / ".task" / "TASK-001" / "manifest.json"
    wj(m, full_task())
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    wj(root / ".task" / "round.json", {"schema_version": 1, "round_id": "R1",
       "expected_windows": ["M2"], "receipts": [], "window_status": {"M2": "pending"},
       "tasks": {"M2": ["TASK-001"]}, "verifier_assignments": {},
       "gears": {"capability": "default", "collaboration": "P"},
       "hook_supervision": False, "check_requested": False})
    run(["gate", "TASK-001", "--dispatch"], root)
    check("P3b 需求就绪后自动建立基线", cpath.is_file(), f"exists={cpath.is_file()}")
    diffs, status = t.contract_diffs(root, "TASK-001", rj(m))
    check("P3c 正常填写后无变更", not diffs and not status, f"diffs={diffs} status={status}")


def P4():
    """可执行 verify 变化必须被发现；且合法变更可被 M1 显式接受。"""
    root = Path(tempfile.mkdtemp(prefix="v4-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    m = root / ".task" / "TASK-001" / "manifest.json"
    wj(m, full_task())
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    wj(root / ".task" / "round.json", {"schema_version": 1, "round_id": "R1",
       "expected_windows": ["M2"], "receipts": [], "window_status": {"M2": "pending"},
       "tasks": {"M2": ["TASK-001"]}, "verifier_assignments": {},
       "gears": {"capability": "default", "collaboration": "P"},
       "hook_supervision": False, "check_requested": False})
    run(["gate", "TASK-001", "--dispatch"], root)
    d = rj(m)
    d["requirements"][0]["verify"] = 'node -e "process.exit(0)"'  # 只改 verify
    wj(m, d)
    diffs, _ = t.contract_diffs(root, "TASK-001", d)
    fields = [item["field"] for item in diffs]
    check("P4a 只改 verify 能被发现", any("verify" in f for f in fields), f"fields={fields}")
    stored = rj(root / ".task" / "TASK-001" / "contract.json")
    check("P4b 基线保留了原需求文字",
          stored["baseline"]["requirements"][0].get("text") == "要能跑"
          and stored["baseline"]["source_refs"][0].get("text") == "用户需求原文",
          json.dumps(stored["baseline"], ensure_ascii=False)[:200])
    code, out = run(["gate", "TASK-001"], root)
    check("P4c 变更时收口被拒", code == 1 and "ACCEPTANCE_CHANGED" in out, f"code={code}")
    check("P4d 报错里给出原标准", "原需求与原标准" in out and "要能跑" in out, out[:300])
    # rev2 Q2：接受必须带**验收者对本次变化的判断**。
    # 先在**同一 root** 上验证"无证据被拒"，再补证据验证"能接受"。
    key = t.acceptance_key(diffs)
    code_no, out_no = run(
        ["adjudicate", "TASK-001", "accept", "--reason", "no verifier report"], root
    )
    check("P4e0 无验收证据时拒绝接受",
          code_no != 0 and "ADJUDICATE_NO_EVIDENCE" in out_no,
          f"code={code_no} out={out_no[:200]}")
    wj(root / ".task" / "TASK-001" / "verify-report.json", {
        "task_id": "TASK-001", "reviewer": "C1", "result": "pass",
        "checked_requirements": ["R1"], "missing": [], "contract_key": key,
    })
    code, out = run(["adjudicate", "TASK-001", "accept", "--reason", "验收者已复核本次变化"], root)
    check("P4e M1 显式接受成功", code == 0 and "ACCEPTANCE_ACCEPTED" in out, f"code={code} out={out[:250]}")
    stored_after = rj(root / ".task" / "TASK-001" / "contract.json")
    judgment = (stored_after.get("accepted_changes") or [{}])[-1].get("judgment") or {}
    check("P4e1 接受记录留住了判断者与依据",
          judgment.get("reviewer") == "C1" and judgment.get("report_hash"),
          json.dumps(judgment, ensure_ascii=False)[:200])
    code, out = run(["gate", "TASK-001"], root)
    check("P4f 接受后不再因该变化被拒", "ACCEPTANCE_CHANGED" not in out, out[:300])


def P5():
    """本轮授权限定：历史任务不得替本轮授权；用户既有改动不应被误报。"""
    root = Path(tempfile.mkdtemp(prefix="v5-"))
    (root / "src").mkdir(); (root / "src" / "a.js").write_text("1\n")
    (root / ".gitignore").write_text(".task/\n")
    git_init(root)
    # 用户既有改动（开轮前就存在、未提交）——必须在 round-init **之前**制造，
    # 这样开轮基线才会把它记成"本来就有"，从而不算本轮越界。
    (root / "user-note.txt").write_text("用户自己的草稿\n")
    code, out = run(["round-init", "ROUND-001", "M2", "--task", "M2=TASK-001"], root)
    check("P5-pre round-init 成功", code == 0, out[:200])
    wj(root / ".task" / "TASK-001" / "manifest.json",
       {**full_task(), "status": "pending", "allowed_paths": ["src/"]})
    # 历史 done 任务曾授权 outside/（不在本轮 round.tasks 里）
    wj(root / ".task" / "TASK-999" / "manifest.json",
       {**full_task(owner="M9"), "task_id": "TASK-999", "status": "done", "allowed_paths": ["outside/"]})
    (root / "outside").mkdir(); (root / "outside" / "rogue.js").write_text("2\n")
    unexplained, authorized = t.round_overrun_files(root, rj(root / ".task" / "round.json"))
    check("P5a 历史任务不再替本轮授权", "outside/rogue.js" in unexplained,
          f"unexplained={unexplained} authorized={authorized}")
    check("P5b 用户既有改动不被误报", "user-note.txt" not in unexplained,
          f"unexplained={unexplained}")


def P6():
    """迁移失败不得留下任何部分修改。"""
    root = Path(tempfile.mkdtemp(prefix="v6-"))
    (root / "src").mkdir(); (root / "src" / "a.py").write_text("print(1)\n")
    git_init(root)
    wj(root / ".task" / "round.json", {"round_id": "R1", "expected_windows": ["M2"],
       "receipts": [], "window_status": {"M2": "pending"}, "tasks": {"M2": ["TASK-001"]}})
    wj(root / ".task" / "TASK-001" / "manifest.json",
       {**full_task(schema_version=None), "status": "pending"})  # 真·旧文件：无版本标记
    wj(root / ".task" / "TASK-002" / "manifest.json",
       {"task_id": "TASK-002", "owner": "M2", "status": "not_a_real_state"})
    (root / "scripts").mkdir(); (root / "scripts" / "taskctl.py").write_text("# old\n")
    before_script = (root / "scripts" / "taskctl.py").read_text(encoding="utf-8")
    code, out = run(["migrate-project", "--destination", "scripts/taskctl.py", "--force"], root)
    t1 = rj(root / ".task" / "TASK-001" / "manifest.json")
    rd = rj(root / ".task" / "round.json")
    script_now = (root / "scripts" / "taskctl.py").read_text(encoding="utf-8")
    check("P6a 迁移如实失败", code == 1 and "MIGRATE_CONVERSION_FAILED" in out, f"code={code}")
    # 旧文件本来就没有 schema_version；断言的是"没有被改成 1"。
    check("P6b 共同文件未被部分写入",
          t1.get("schema_version") is None and rd.get("schema_version") is None,
          f"t1={t1.get('schema_version')} round={rd.get('schema_version')}")
    check("P6c 目标脚本保持原样", script_now == before_script, "脚本被替换了")


def P7():
    """缺关键字段不得被标成功或 MIGRATE_READY。"""
    root = Path(tempfile.mkdtemp(prefix="v7-"))
    (root / "src").mkdir(); (root / "src" / "a.py").write_text("print(1)\n")
    git_init(root)
    wj(root / ".task" / "TASK-002" / "manifest.json",
       {"task_id": "TASK-002", "status": "pending"})  # 缺 owner/allowed_paths/requirements
    (root / "scripts").mkdir(); (root / "scripts" / "taskctl.py").write_text("# old\n")
    code, out = run(["migrate-project", "--destination", "scripts/taskctl.py", "--force"], root)
    t2 = rj(root / ".task" / "TASK-002" / "manifest.json")
    check("P7a 缺关键字段不被标兼容", code == 1 and "MIGRATE_CONVERSION_FAILED" in out,
          f"code={code} schema={t2.get('schema_version')}")
    code2, out2 = run(["migrate-project", "--check"], root)
    check("P7b 不得给出 MIGRATE_READY", "MIGRATE_READY" not in out2, out2[:200])


def main() -> int:
    """退出码必须反映结果：有 FAIL 就非 0。

    （Codex rev2 §6 指出：早先 check() 只打印，main() 恒返回 0，
    所以"退出 0"不能用来判断全部通过。）
    """
    print("=== 复验 Codex 审阅的 7 条（反例 + 合法正例） ===")
    for probe in (P1, P2, P3, P4, P5, P6, P7):
        print(f"\n--- {probe.__name__} ---")
        probe()
    print()
    if FAILURES:
        print(f"== {len(FAILURES)} 项 FAIL：{'; '.join(FAILURES)} ==")
        return 1
    print("== 全部通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
