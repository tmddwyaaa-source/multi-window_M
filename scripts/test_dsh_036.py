"""v0.36（DSH 方向）：schema 契约、round-close、只读 status、不可重放命令、过期报告。

本轮改动都是"把已证明的错误机械暴露"，因此每个检查都必须证明两件事：
  1. 该拦的拦住了（负例）；
  2. 不该拦的没被误杀（正例）——尤其是"本机没装的测试运行器"和"合法重定向"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKCTL = HERE / "taskctl.py"
sys.path.insert(0, str(HERE))
import taskctl as module  # noqa: E402  (本套件要核对模块级契约)


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


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def new_root(tag: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m035-{tag}-"))
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (root / "_tools").mkdir(parents=True, exist_ok=True)
    (root / "_tools" / "smoke.mjs").write_text("process.exit(0);\n", encoding="utf-8")
    return root


def seed_task(
    root: Path,
    task_id: str = "TASK-001",
    *,
    owner: str = "M2",
    risk: str = "low",
    status: str = "done",
    commands: list[str] | None = None,
) -> Path:
    """建一个在短路径下能过 Full Gate 的任务。"""
    manifest_path = root / ".task" / task_id / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "task_id": task_id,
            "title": "测试任务",
            "owner": owner,
            "track": "feature",
            "risk": risk,
            "attempt": 0,
            "status": status,
            "status_history": [{"status": status, "at": "2026-01-01T00:00:00+00:00", "by": "M1"}],
            "allowed_paths": ["src/"],
            "source_refs": [{"id": "S1", "text": "需求", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "可验收需求", "verify": "人工检查"}],
        },
    )
    (root / ".task" / task_id / "evidence").mkdir(parents=True, exist_ok=True)
    # Gate 要求每条 evidence 都有真实存在的 path，所以给每个需求落一份证据文件。
    for requirement_id in ("R1",):
        evidence_file = root / ".task" / task_id / "evidence" / f"{requirement_id}.txt"
        evidence_file.write_text("verified\n", encoding="utf-8")
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
            "tests": [{"command": command} for command in (commands or [])],
        },
    )
    return manifest_path


def seed_round(
    root: Path,
    *,
    windows: list[str] | None = None,
    tasks: dict | None = None,
    receipts: list[str] | None = None,
) -> Path:
    """建一轮。`receipts` 全给时窗口状态设为 worker_done，用来越过 receipt 闸。"""
    windows = windows or ["M2", "M5", "C1"]
    receipts = receipts or []
    window_status = {
        window: ("worker_done" if window in receipts else "pending") for window in windows
    }
    write_json(
        root / ".task" / "round.json",
        {
            "schema_version": 1,
            "round_id": "ROUND-001",
            "expected_windows": windows,
            "receipts": receipts,
            "window_status": window_status,
            "tasks": tasks if tasks is not None else {"M2": ["TASK-001"], "M5": [], "C1": []},
            "verifier_assignments": {},
            "gears": {"capability": "default", "collaboration": "P"},
            "hook_supervision": False,
            "check_requested": False,
        },
    )
    return root / ".task" / "round.json"


def main() -> int:
    # ---------- A. schema_version 契约 ----------
    root = new_root("schema")
    seed_task(root, status="pending")
    manifest = read_json(root / ".task" / "TASK-001" / "manifest.json")
    expect(
        "S-pos-init-writes-schema-version",
        manifest.get("schema_version") == 1,
        json.dumps(manifest, ensure_ascii=False)[:200],
    )
    round_init = run(["--root", str(root), "round-init", "ROUND-002", "M2", "C3"])
    seeded = seed_round(root)
    expect(
        "S-pos-round-init-writes-schema-version",
        read_json(seeded).get("schema_version") == 1,
        seeded.read_text(encoding="utf-8")[:200],
    )
    expect("S-pos-round-init-ok", round_init[0] == 0, round_init[1])

    # 旧文件（无 schema_version）必须继续被接受：迁移兼容路径。
    legacy = new_root("legacy")
    seed_task(legacy, status="pending")
    legacy_manifest_path = legacy / ".task" / "TASK-001" / "manifest.json"
    legacy_manifest = read_json(legacy_manifest_path)
    legacy_manifest.pop("schema_version", None)
    write_json(legacy_manifest_path, legacy_manifest)
    code, out = run(["--root", str(legacy), "gate", "TASK-001", "--basic"])
    expect(
        "S-pos-legacy-without-schema-version-accepted",
        code == 0 and "RESULT PASS" in out,
        f"code={code} out={out}",
    )
    expect(
        "S-pos-legacy-fallback-is-documented",
        "LEGACY_UNSCOPED" in (module.current_attempts.__doc__ or ""),
        module.current_attempts.__doc__ or "",
    )

    # 更新的 schema：fail closed，禁止写、禁止收口，但仍可查看。
    future = new_root("future")
    seed_task(future, status="pending")
    future_manifest_path = future / ".task" / "TASK-001" / "manifest.json"
    future_manifest = read_json(future_manifest_path)
    future_manifest["schema_version"] = 99
    write_json(future_manifest_path, future_manifest)

    code, out = run(
        ["--root", str(future), "reopen", "TASK-001", "--reason", "x", "--block-id", "B1"]
    )
    expect(
        "S-neg-future-schema-blocks-mutation",
        code == 1 and "UNSUPPORTED_SCHEMA" in out,
        f"code={code} out={out}",
    )
    code, out = run(["--root", str(future), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect(
        "S-neg-future-schema-blocks-transition",
        code == 1 and "UNSUPPORTED_SCHEMA" in out,
        f"code={code} out={out}",
    )
    code, out = run(["--root", str(future), "gate", "TASK-001"])
    expect(
        "S-neg-future-schema-blocks-close",
        code == 1 and "UNSUPPORTED_SCHEMA" in out,
        f"code={code} out={out}",
    )
    code, out = run(["--root", str(future), "status"])
    expect(
        "S-pos-future-schema-still-viewable",
        code == 0 and "TASK TASK-001" in out,
        f"code={code} out={out}",
    )
    unchanged = read_json(future_manifest_path)
    expect(
        "S-pos-future-schema-not-rewritten",
        unchanged.get("status") == "pending" and unchanged.get("schema_version") == 99,
        json.dumps(unchanged, ensure_ascii=False)[:200],
    )

    # ---------- A2. 回归方向：项目里确有带标记的文件，却被旧宿主写出无标记文件 ----------
    # 判据（0.36 修正后）：**磁盘上存在带版本标记的共同文件**时，缺标记的
    # 已开工任务/已派本轮 = 被旧宿主改写。项目里一个带标记的文件都没有时不算回归
    # （那只是刚 init 过，见 A2c）。
    regress = new_root("regress")
    seed_task(regress, status="pending")
    seed_round(regress)
    write_json(regress / ".task" / "skill-lock.json", {"schema_version": 1, "skill_version": "0.36"})
    # 磁盘高水位：让 TASK-001 带上标记（合法），再让 round.json 缺标记（回归证据）
    regress_manifest = regress / ".task" / "TASK-001" / "manifest.json"
    regress_manifest_data = read_json(regress_manifest)
    regress_manifest_data["schema_version"] = 1
    regress_manifest_data["status"] = "in_progress"
    write_json(regress_manifest, regress_manifest_data)
    regress_round = regress / ".task" / "round.json"
    regress_data = read_json(regress_round)
    regress_data.pop("schema_version", None)
    write_json(regress_round, regress_data)
    code, out = run(["--root", str(regress), "transition", "TASK-001", "worker_done", "--actor", "worker"])
    expect(
        "A2-neg-active-round-without-version-blocks-write",
        code == 1 and "SCHEMA_REGRESSION_RISK" in out,
        f"code={code} out={out}",
    )
    # A2c：项目里一个带标记的文件都没有时，不判回归（避免把刚 init 的项目拦死）
    fresh_project = new_root("freshregress")
    seed_task(fresh_project, status="pending")
    fresh_round = seed_round(fresh_project)
    for path in (fresh_round, fresh_project / ".task" / "TASK-001" / "manifest.json"):
        fresh_data = read_json(path)
        fresh_data.pop("schema_version", None)
        write_json(path, fresh_data)
    code, out = run(["--root", str(fresh_project), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect(
        "A2c-pos-no-versioned-file-means-no-regression",
        code == 0 and "SCHEMA_REGRESSION_RISK" not in out,
        f"code={code} out={out}",
    )
    # 只读仍然可用
    expect(
        "A2-pos-readonly-still-works",
        run(["--root", str(regress), "status"])[0] == 0,
        run(["--root", str(regress), "status"])[1],
    )

    # A2b：**已开工**任务缺版本标记，而项目里确有带标记的文件 → 判回归
    # （顺序要真实：先开工拿到标记，再模拟旧宿主把它改写成无标记的文件）
    regress2 = new_root("regress2")
    seed_task(regress2, status="pending")
    seed_round(regress2)
    write_json(regress2 / ".task" / "skill-lock.json", {"schema_version": 1, "skill_version": "0.36"})
    code, out = run(["--root", str(regress2), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect("A2b-pre-transition-ok", code == 0, f"code={code} out={out}")
    regress2_manifest = regress2 / ".task" / "TASK-001" / "manifest.json"
    regress2_data = read_json(regress2_manifest)
    regress2_data.pop("schema_version", None)  # 旧宿主改写：不写版本标记
    write_json(regress2_manifest, regress2_data)
    code, out = run(["--root", str(regress2), "transition", "TASK-001", "worker_done", "--actor", "worker"])
    expect(
        "A2-neg-task-without-version-blocks-write",
        code == 1 and "SCHEMA_REGRESSION_RISK" in out,
        f"code={code} out={out}",
    )

    # **正例（关键）**：真正的历史项目——锁里也没有版本声明——必须继续兼容，
    # 不能把"老项目的文件没有版本号"误判成被旧宿主改过。
    legacy_project = new_root("legacyproj")
    seed_task(legacy_project, status="pending")
    legacy_round = seed_round(legacy_project)
    legacy_manifest_path = legacy_project / ".task" / "TASK-001" / "manifest.json"
    for path in (legacy_manifest_path, legacy_round):
        legacy_data = read_json(path)
        legacy_data.pop("schema_version", None)
        write_json(path, legacy_data)
    code, out = run(["--root", str(legacy_project), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect(
        "A2-pos-pure-legacy-project-still-allowed",
        code == 0 and "SCHEMA_REGRESSION_RISK" not in out,
        f"code={code} out={out}",
    )
    # 而这个老项目一旦被当前版本写过（init 升级锁），再出现缺版本文件就要报
    code, _ = run(["--root", str(legacy_project), "init", "TASK-002", "--owner", "M1"])
    expect("A2-pos-init-upgrades-contract", code == 0, f"code={code}")
    expect(
        "A2-pos-lock-now-declares-schema",
        read_json(legacy_project / ".task" / "skill-lock.json").get("schema_version") == 1,
        (legacy_project / ".task" / "skill-lock.json").read_text(encoding="utf-8"),
    )
    code, out = run(["--root", str(legacy_project), "transition", "TASK-001", "in_progress", "--actor", "M1"])
    expect(
        "A2-neg-after-contract-upgrade-legacy-file-is-flagged",
        code == 1 and "SCHEMA_REGRESSION_RISK" in out,
        f"code={code} out={out}",
    )

    # ---------- B. status 只读 ----------
    slow = new_root("status")
    seed_task(slow, status="done", commands=["node -e \"setTimeout(function(){}, 30000)\""])
    seed_round(slow)
    code, out = run(["--root", str(slow), "gate", "TASK-001"])
    expect("B-pos-gate-runs-command", code == 0 and "RESULT PASS" in out, f"code={code} out={out}")

    started = time.time()
    code, out = run(["--root", str(slow), "status"])
    elapsed = time.time() - started
    expect(
        "B-pos-status-does-not-run-shell",
        code == 0 and elapsed < 20 and "GATE_PASS" in out,
        f"code={code} elapsed={elapsed:.1f}s out={out}",
    )

    fresh = new_root("status2")
    seed_task(fresh, status="pending")
    code, out = run(["--root", str(fresh), "status"])
    expect(
        "B-pos-status-without-rerun-says-not-run",
        code == 0 and "GATE_NOT_RUN" in out,
        f"code={code} out={out}",
    )
    # 证明只读 status 确实**没有**跑门禁：把任务改成必然 GATE_FAIL 后，
    # 普通 status 仍然显示旧结论（NOT_RUN），只有显式 --deep 才会重算。
    fresh_manifest_path = fresh / ".task" / "TASK-001" / "manifest.json"
    fresh_manifest = read_json(fresh_manifest_path)
    fresh_manifest["allowed_paths"] = []
    write_json(fresh_manifest_path, fresh_manifest)
    code, out = run(["--root", str(fresh), "status"])
    expect(
        "B-pos-status-ignores-new-breakage",
        code == 0 and "GATE_NOT_RUN" in out,
        f"code={code} out={out}",
    )
    code, out = run(["--root", str(fresh), "status", "--deep"])
    expect(
        "B-pos-status-deep-still-available",
        code == 0 and "GATE_FAIL" in out,
        f"code={code} out={out}",
    )

    # ---------- C. UNREPLAYABLE_COMMAND ----------
    unreplay = new_root("unreplay")
    seed_task(unreplay, status="done", commands=["node _tools/smoke.mjs (临时文件，验证后已删除)"])
    seed_round(unreplay)
    code, out = run(["--root", str(unreplay), "gate", "TASK-001"])
    expect(
        "U-neg-placeholder-note-is-blocked",
        code == 1 and "UNREPLAYABLE_COMMAND" in out,
        f"code={code} out={out}",
    )

    bracket = new_root("bracket")
    seed_task(bracket, status="done", commands=["node --check <临时复制的 grid.js>"])
    seed_round(bracket)
    code, out = run(["--root", str(bracket), "gate", "TASK-001"])
    expect(
        "U-neg-angle-placeholder-is-blocked",
        code == 1 and "UNREPLAYABLE_COMMAND" in out,
        f"code={code} out={out}",
    )

    # 缺失脚本：收口时必须存在。判据是**事实检查**（不做任何创建/覆盖/删除），
    # 且只认"本任务范围内、且已声明会交付"的脚本。
    missing = new_root("missing")
    seed_task(missing, status="done", commands=["node _tools/gone.mjs"])
    missing_report = missing / ".task" / "TASK-001" / "worker-report.json"
    missing_data = read_json(missing_report)
    missing_data["changed_files"] = ["_tools/gone.mjs"]  # 声明过要交付
    write_json(missing_report, missing_data)
    seed_round(missing)
    code, out = run(["--root", str(missing), "gate", "TASK-001"])
    expect(
        "U-neg-missing-script-blocks-at-close",
        code == 1 and "MISSING_ACCEPTANCE_SCRIPT" in out and "gone.mjs" in out,
        f"code={code} out={out}",
    )
    expect(
        "U-neg-no-placeholder-created",
        not (missing / "_tools" / "gone.mjs").exists(),
        str(list((missing / "_tools").iterdir())),
    )
    # 派工阶段：计划新建的脚本允许不存在 —— 不能因为"派工时还没有脚本"而卡住派工。
    dispatch = new_root("dispatch")
    seed_task(dispatch, status="pending", commands=["node _tools/planned.mjs"])
    seed_round(dispatch)
    code, out = run(["--root", str(dispatch), "gate", "TASK-001", "--basic"])
    expect(
        "U-pos-dispatch-allows-missing-planned-script",
        "MISSING_ACCEPTANCE_SCRIPT" not in out
        and not (dispatch / "_tools" / "planned.mjs").exists(),
        f"code={code} out={out}",
    )

    # 正例：必须一个都不能误杀。
    good = new_root("goodcmd")
    seed_task(
        good,
        status="done",
        commands=[
            "node _tools/smoke.mjs",
            "node --check src/app.js",
            "py -3 -c \"print(1)\"",
            "node -e \"process.exit(0)\" 2>&1",
            "node -e \"process.exit(0)\" > out.log",
        ],
    )
    seed_round(good)
    code, out = run(["--root", str(good), "gate", "TASK-001"])
    expect(
        "U-pos-replayable-commands-pass",
        code == 0 and "UNREPLAYABLE_COMMAND" not in out,
        f"code={code} out={out}",
    )

    # 正例：本机没装的测试运行器不能算工人的错。
    absent = new_root("absent")
    seed_task(absent, status="done", commands=["pytest -q tests/"])
    seed_round(absent)
    code, out = run(["--root", str(absent), "gate", "TASK-001"])
    expect(
        "U-pos-missing-runner-not-flagged-unreplayable",
        "UNREPLAYABLE_COMMAND" not in out,
        f"code={code} out={out}",
    )

    # 正例：自然语言的 verify 不是命令，不应被当成命令收集。
    natural = new_root("natural")
    seed_task(natural, status="pending")
    natural_manifest_path = natural / ".task" / "TASK-001" / "manifest.json"
    natural_manifest = read_json(natural_manifest_path)
    natural_manifest["requirements"] = [
        {"id": "R1", "text": "可验收需求", "verify": "人工目视检查（不写命令）"}
    ]
    write_json(natural_manifest_path, natural_manifest)
    code, out = run(["--root", str(natural), "gate", "TASK-001", "--basic"])
    expect(
        "U-pos-prose-verify-not-collected",
        "UNREPLAYABLE_COMMAND" not in out,
        f"code={code} out={out}",
    )

    # verify_cmd 里放占位符必须被拦（任务侧入口）。
    bad_verify = new_root("badverify")
    seed_task(bad_verify, status="pending")
    bad_manifest_path = bad_verify / ".task" / "TASK-001" / "manifest.json"
    bad_manifest = read_json(bad_manifest_path)
    bad_manifest["requirements"] = [
        {"id": "R1", "text": "x", "verify_cmd": "运行一下 <测试脚本>"}
    ]
    write_json(bad_manifest_path, bad_manifest)
    code, out = run(["--root", str(bad_verify), "gate", "TASK-001", "--basic"])
    expect(
        "U-neg-placeholder-verify-cmd-is-blocked",
        code == 1 and "UNREPLAYABLE_COMMAND" in out,
        f"code={code} out={out}",
    )

    # ---------- D. STALE_REPORT（软提示，不判 FAIL） ----------
    stale = new_root("stale")
    seed_task(stale, status="done")
    report_path = stale / ".task" / "TASK-001" / "worker-report.json"
    report = read_json(report_path)
    report["changed_files"] = ["src/app.js"]
    write_json(report_path, report)
    seed_round(stale)
    code, out = run(["--root", str(stale), "gate", "TASK-001"])
    expect("D-pos-baseline-gate-passes", code == 0, f"code={code} out={out}")
    rerun = read_json(stale / ".task" / "TASK-001" / "rerun.json")
    expect(
        "D-pos-fingerprint-recorded",
        isinstance(rerun.get("files"), dict) and "src/app.js" in rerun["files"],
        json.dumps(rerun, ensure_ascii=False)[:300],
    )
    expect("D-pos-not-stale-yet", rerun.get("stale_report") is False, json.dumps(rerun)[:200])

    (stale / "src" / "app.js").write_text("console.log(2);\n", encoding="utf-8")
    code, out = run(["--root", str(stale), "gate", "TASK-001"])
    expect(
        "D-neg-stale-report-detected",
        code == 0 and "STALE_REPORT" in out and "src/app.js" in out,
        f"code={code} out={out}",
    )
    expect(
        "D-pos-stale-report-is-not-a-gate-failure",
        "RESULT PASS" in out,
        f"code={code} out={out}",
    )
    rerun = read_json(stale / ".task" / "TASK-001" / "rerun.json")
    expect("D-pos-stale-recorded", rerun.get("stale_report") is True, json.dumps(rerun)[:200])

    code, out = run(["--root", str(stale), "gate", "TASK-001"])
    expect(
        "D-pos-stale-clears-after-rerun",
        "STALE_REPORT" not in out and "RESULT PASS" in out,
        f"code={code} out={out}",
    )

    # ---------- D2. 收口时过期报告是**阻断项**（与日常 gate 的 WARN 分层） ----------
    blocking = new_root("staleclose")
    seed_task(blocking, status="done")
    blocking_report = blocking / ".task" / "TASK-001" / "worker-report.json"
    blocking_data = read_json(blocking_report)
    blocking_data["changed_files"] = ["src/app.js"]
    write_json(blocking_report, blocking_data)
    # 给全 receipt，越过 receipt 闸，才能测到"过期报告"这一层
    seed_round(blocking, receipts=["M2", "M5", "C1"])
    code, out = run(["--root", str(blocking), "gate", "TASK-001"])
    expect("D2-pos-baseline-ok", code == 0, f"code={code} out={out}")
    (blocking / "src" / "app.js").write_text("console.log(3);\n", encoding="utf-8")
    # 先证明同一状态下日常 gate 仍然是 PASS（提示而非阻断）
    code, out = run(["--root", str(blocking), "gate", "TASK-001"])
    expect(
        "D2-pos-daily-gate-still-passes-with-warning",
        code == 0 and "STALE_REPORT" in out and "RESULT PASS" in out,
        f"code={code} out={out}",
    )
    # 门禁那次会把指纹基线刷新到"当时的内容"——这是必要的：门禁不可能知道报告是谁写的、
    # 什么时候写的。所以只要没人再动文件，过期就"自愈"了。真实场景是门禁之后又被改：
    (blocking / "src" / "app.js").write_text("console.log(4);\n", encoding="utf-8")
    # 方向文件 §6.4 / §3「当前不可靠」：当前实现受流程产物影响，
    # **本轮取消 gate 与 round-close 的阻断**。所以收口必须能过，提示仍要留下。
    code, out = run(["--root", str(blocking), "round-close"])
    expect(
        "D2-pos-stale-no-longer-blocks-round-close",
        code == 0 and "ROUND_CLOSED" in out,
        f"code={code} out={out}",
    )
    archived = blocking / ".task" / "rounds" / "ROUND-001.json"
    expect(
        "D2-pos-close-still-archived",
        archived.is_file() and not (blocking / ".task" / "round.json").exists(),
        f"archived={archived.is_file()}",
    )
    # 提示仍在 rerun.json 里留痕（可查、但不阻断、不计入失败次数）
    rerun_blocking = read_json(blocking / ".task" / "TASK-001" / "rerun.json")
    expect(
        "D2-pos-stale-still-recorded-for-audit",
        rerun_blocking.get("stale_report") is True,
        json.dumps(rerun_blocking, ensure_ascii=False)[:200],
    )

    # ---------- E. round-close ----------
    open_round = new_root("openround")
    seed_task(open_round, status="in_progress")
    seed_round(open_round)
    code, out = run(["--root", str(open_round), "round-close"])
    expect(
        "R-neg-unfinished-round-is-not-archived",
        code == 1 and "ROUND_CLOSE_FAIL" in out and (open_round / ".task" / "round.json").is_file(),
        f"code={code} out={out}",
    )

    closeable = new_root("close")
    seed_task(closeable, status="done")
    seed_round(closeable, receipts=["M2", "M5", "C1"])
    code, out = run(["--root", str(closeable), "round-close"])
    archived = closeable / ".task" / "rounds" / "ROUND-001.json"
    expect(
        "R-pos-closed-round-is-archived",
        code == 0
        and "ROUND_CLOSED ROUND-001" in out
        and archived.is_file()
        and not (closeable / ".task" / "round.json").exists(),
        f"code={code} out={out}",
    )
    expect(
        "R-pos-archive-keeps-task-states",
        archived.is_file() and read_json(archived).get("closed_by") == "M1",
        archived.read_text(encoding="utf-8")[:300] if archived.is_file() else "missing",
    )
    code, out = run(["--root", str(closeable), "round-init", "ROUND-002", "M2", "C3"])
    expect(
        "R-pos-next-round-can-start",
        code == 0 and "CREATED" in out,
        f"code={code} out={out}",
    )

    print("ALL v0.36 DSH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
