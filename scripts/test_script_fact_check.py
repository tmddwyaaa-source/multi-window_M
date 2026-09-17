"""0.36 修正验收：必需验收脚本的**事实检查**，不做任何创建/覆盖/删除。

按《给 DSH：0.36 验收修正》第三节的五个场景逐项验证，
并且每条都检查**真实的前后文件状态**——不只是断言 token 或退出码。

被否掉的做法（本套件存在的理由）：原先实现会在缺失时**临时创建空壳**再执行。
它至少有两个错：空脚本不必然失败（空 Python/JS 可能退出 0），
而且创建空壳改变了被检查对象，无法证明原交付命令可运行。
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


def git_snapshot(root: Path) -> dict:
    """记录业务/测试目录下的**真实文件内容**，用于前后对比。"""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".task/") or rel.startswith(".git/") or "__pycache__" in rel:
            continue
        snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
    return snapshot


def new_root(tag: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m036-scripts-{tag}-"))
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (root / "_tools").mkdir(parents=True)
    return root


def seed(root: Path, *, status: str, commands: list[str], changed: list[str] | None = None) -> None:
    write_json(
        root / ".task" / "TASK-001" / "manifest.json",
        {
            "schema_version": 1,
            "task_id": "TASK-001",
            "title": "脚本事实检查",
            "owner": "M2",
            "track": "feature",
            "risk": "low",
            "attempt": 0,
            "status": status,
            "status_history": [{"status": status, "at": "2026-01-01T00:00:00+00:00", "by": "M1"}],
            "allowed_paths": ["src/", "_tools/"],
            "source_refs": [{"id": "S1", "text": "t", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "x", "verify": "人工检查"}],
        },
    )
    evidence = root / ".task" / "TASK-001" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "R1.txt").write_text("ok\n", encoding="utf-8")
    write_json(
        root / ".task" / "TASK-001" / "worker-report.json",
        {
            "task_id": "TASK-001",
            "window": "M2",
            "status": "worker_done",
            "covered_requirements": ["R1"],
            "evidence": [
                {"requirement_id": "R1", "path": ".task/TASK-001/evidence/R1.txt", "note": "ok"}
            ],
            "changed_files": changed or [],
            "known_gaps": [],
            "tests": [{"command": c} for c in commands],
        },
    )
    write_json(
        root / ".task" / "round.json",
        {
            "schema_version": 1,
            "round_id": "ROUND-001",
            "expected_windows": ["M2"],
            "receipts": [],
            "window_status": {"M2": "pending"},
            "tasks": {"M2": ["TASK-001"]},
            "verifier_assignments": {},
            "gears": {"capability": "default", "collaboration": "P"},
            "hook_supervision": False,
            "check_requested": False,
        },
    )


def main() -> int:
    # 场景 1：派工引用计划新建的脚本 → 派工成功，脚本路径仍不存在
    dispatch = new_root("dispatch")
    seed(dispatch, status="pending", commands=["node _tools/planned.mjs"])
    before = git_snapshot(dispatch)
    code, out = run(["gate", "TASK-001", "--basic"], dispatch)
    after = git_snapshot(dispatch)
    expect(
        "S1-pos-dispatch-succeeds-and-creates-nothing",
        "MISSING_ACCEPTANCE_SCRIPT" not in out
        and not (dispatch / "_tools" / "planned.mjs").exists()
        and before == after,
        f"code={code} exists={(dispatch / '_tools' / 'planned.mjs').exists()} out={out[:300]}",
    )

    # 场景 2：收口引用必需但缺失的脚本 → 失败，路径前后都不存在，不产生空壳
    close_case = new_root("close")
    seed(
        close_case,
        status="done",
        commands=["node _tools/required.mjs"],
        changed=["_tools/required.mjs"],  # 声明过要交付
    )
    before = git_snapshot(close_case)
    code, out = run(["gate", "TASK-001"], close_case)
    after = git_snapshot(close_case)
    expect(
        "S2-neg-missing-required-script-fails-close",
        code == 1 and "MISSING_ACCEPTANCE_SCRIPT" in out and "required.mjs" in out,
        f"code={code} out={out}",
    )
    expect(
        "S2-neg-no-placeholder-created-and-no-mutation",
        not (close_case / "_tools" / "required.mjs").exists() and before == after,
        f"exists={(close_case / '_tools' / 'required.mjs').exists()} "
        f"added={sorted(set(after) - set(before))} changed="
        f"{sorted(k for k in before if k in after and before[k] != after[k])}",
    )

    # 场景 3：真实脚本存在且返回失败 → 验收失败，脚本内容不变
    failing = new_root("failing")
    (failing / "_tools" / "check.mjs").write_text("process.exit(3);\n", encoding="utf-8")
    seed(
        failing,
        status="done",
        commands=["node _tools/check.mjs"],
        changed=["_tools/check.mjs"],
    )
    before = git_snapshot(failing)
    code, out = run(["gate", "TASK-001"], failing)
    after = git_snapshot(failing)
    expect(
        "S3-neg-real-failing-script-fails-acceptance",
        code == 1 and "verify command failed (exit 3)" in out,
        f"code={code} out={out}",
    )
    expect(
        "S3-pos-script-content-unchanged",
        before == after
        and after.get("_tools/check.mjs") == "process.exit(3);\n",
        f"changed={sorted(k for k in before if before.get(k) != after.get(k))}",
    )

    # 场景 4：真实脚本存在且检查通过 → 验收成功，脚本内容不变
    passing = new_root("passing")
    (passing / "_tools" / "check.mjs").write_text("process.exit(0);\n", encoding="utf-8")
    seed(
        passing,
        status="done",
        commands=["node _tools/check.mjs"],
        changed=["_tools/check.mjs"],
    )
    before = git_snapshot(passing)
    code, out = run(["gate", "TASK-001"], passing)
    after = git_snapshot(passing)
    expect(
        "S4-pos-real-passing-script-passes",
        code == 0 and "RESULT PASS" in out,
        f"code={code} out={out}",
    )
    expect(
        "S4-pos-script-content-unchanged",
        before == after and after.get("_tools/check.mjs") == "process.exit(0);\n",
        f"changed={sorted(k for k in before if before.get(k) != after.get(k))}",
    )

    # 场景 5：只交空脚本时，通用工具**不能**识别"没有有效断言" —— 必须诚实说明限制
    empty = new_root("emptyscript")
    (empty / "_tools" / "empty.mjs").write_text("", encoding="utf-8")
    seed(
        empty,
        status="done",
        commands=["node _tools/empty.mjs"],
        changed=["_tools/empty.mjs"],
    )
    code, out = run(["gate", "TASK-001"], empty)
    expect(
        "S5-limitation-empty-script-exits-zero-and-tool-cannot-tell",
        code == 0,
        f"code={code} out={out}",
    )
    print(
        "NOTE S5：空脚本 `node empty.mjs` 等价于无断言且退出 0，通用工具**无法**由此判断功能；"
        "这一点是已知限制，必须由验收者按原需求检查真实行为，不得把退出 0 当功能通过。"
    )

    # 补充：不得因为"路径不属于本任务"就误判（避免误伤别的任务的脚本）
    outsider = new_root("outsider")
    seed(outsider, status="done", commands=["node other/thing.mjs"], changed=[])
    before = git_snapshot(outsider)
    code, out = run(["gate", "TASK-001"], outsider)
    after = git_snapshot(outsider)
    expect(
        "S6-pos-foreign-path-not-judged-as-this-task-missing",
        "MISSING_ACCEPTANCE_SCRIPT" not in out and before == after,
        f"code={code} out={out[:300]}",
    )

    print("ALL v0.36 SCRIPT-FACT CHECK SCENARIOS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
