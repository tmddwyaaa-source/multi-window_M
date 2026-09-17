"""复现并验证 Codex 报的"迁移死锁"是否已被修掉。

背景（0.35 实测报告 §4.26）：旧项目跑 `migrate-project --force` 后，
`migrate-project --check` 报 20 条 `SCHEMA_REGRESSION_RISK`
（`.task/round.json` 与每个 manifest 都 `declares schema_version=0 but the project declares 1`），
每个从 ≤0.34 迁移上来的项目都会被卡住。

0.36 的修法（方向文件 §6.3）：备份 → 识别旧格式 → **转换**（只补版本标记）→ 验证 → 最后提交版本标记。
本脚本构造一个"旧项目"并完整走一遍，断言不再出现死锁。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKCTL = HERE / "taskctl.py"


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


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_legacy_project(root: Path) -> None:
    """构造一个 0.34 形态的旧项目：有 .task，所有共同文件都没有 schema_version。"""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.email=legacy@example.invalid",
            "-c", "user.name=legacy",
            "commit", "-q", "-m", "legacy baseline",
        ],
        check=True,
        capture_output=True,
    )
    write_json(
        root / ".task" / "round.json",
        {
            "round_id": "ROUND-001",
            "expected_windows": ["M2", "C1"],
            "receipts": [],
            "window_status": {"M2": "pending", "C1": "pending"},
            "tasks": {"M2": ["TASK-001"], "C1": []},
            "hook_supervision": False,
            "check_requested": False,
        },
    )
    write_json(
        root / ".task" / "TASK-001" / "manifest.json",
        {
            "task_id": "TASK-001",
            "title": "旧任务",
            "owner": "M2",
            "track": "feature",
            "risk": "low",
            "attempt": 0,
            "status": "pending",
            "status_history": [{"status": "pending", "at": "2026-01-01T00:00:00+00:00", "by": "taskctl"}],
            "allowed_paths": ["src/"],
            "source_refs": [{"id": "S1", "text": "t", "maps_to": ["R1"]}],
            "requirements": [{"id": "R1", "text": "x", "verify": "y"}],
        },
    )
    # 旧项目里已经有一份 0.34 的 taskctl
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "taskctl.py").write_text("# old taskctl\n", encoding="utf-8")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m036-legacy-migrate-"))
    build_legacy_project(root)

    manifest_path = root / ".task" / "TASK-001" / "manifest.json"
    round_path = root / ".task" / "round.json"

    # 迁移前：旧项目应当**可写**（不是回归）
    code, out = run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    expect(
        "L-pos-legacy-writable-before-migrate",
        code == 0 and "SCHEMA_REGRESSION_RISK" not in out,
        f"code={code} out={out}",
    )
    # 基线在状态稳定之后再取，否则会把这次合法转移算成"迁移改了语义"
    before_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 迁移
    code, out = run(
        ["migrate-project", "--destination", "scripts/taskctl.py", "--force"], root
    )
    expect(
        "L-pos-migrate-succeeds",
        code == 0 and "CONVERTED" in out and "MIGRATE_CONVERSION_FAILED" not in out,
        f"code={code} out={out}",
    )

    # 关键：既有共同文件被转换（只补版本标记），且语义字段原样保留
    migrated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expect(
        "L-pos-manifest-got-version-marker",
        migrated_manifest.get("schema_version") == 1,
        json.dumps(migrated_manifest, ensure_ascii=False)[:300],
    )
    semantic_keys = (
        "task_id", "title", "owner", "track", "risk", "attempt", "status",
        "allowed_paths", "source_refs", "requirements",
    )
    differing = [
        key for key in semantic_keys if migrated_manifest.get(key) != before_manifest.get(key)
    ]
    expect(
        "L-pos-manifest-semantics-preserved",
        not differing,
        f"differing={differing} "
        f"before={ {k: before_manifest.get(k) for k in differing} } "
        f"after={ {k: migrated_manifest.get(k) for k in differing} }",
    )
    expect(
        "L-pos-round-got-version-marker",
        json.loads(round_path.read_text(encoding="utf-8")).get("schema_version") == 1,
        round_path.read_text(encoding="utf-8")[:200],
    )

    # 迁移后：不得再出现 20 条 SCHEMA_REGRESSION_RISK 死锁
    code, out = run(["transition", "TASK-001", "worker_done", "--actor", "worker"], root)
    expect(
        "L-neg-no-migration-deadlock-after-migrate",
        "SCHEMA_REGRESSION_RISK" not in out and "status_history" not in out,
        f"code={code} out={out}",
    )
    code, out = run(["migrate-project", "--check"], root)
    expect(
        "L-pos-check-ready",
        code == 0 and "MIGRATE_READY" in out and "SCHEMA_REGRESSION_RISK" not in out,
        f"code={code} out={out}",
    )

    # 失败的迁移不得留下"成功标记 + 无法操作"的半迁移状态
    broken = Path(tempfile.mkdtemp(prefix="m036-broken-migrate-"))
    build_legacy_project(broken)
    # 追加一个**不兼容**的任务：缺信息必须被报告，不能被编造默认值蒙混过去。
    write_json(
        broken / ".task" / "TASK-002" / "manifest.json",
        {
            "task_id": "TASK-002",
            "owner": "M2",
            "status": "not_a_real_state",  # 当前规则不认识 → 转换必须失败并报告
        },
    )
    code, out = run(
        ["migrate-project", "--destination", "scripts/taskctl.py", "--force"], broken
    )
    expect(
        "L-neg-incompatible-file-fails-loudly",
        code == 1 and "MIGRATE_CONVERSION_FAILED" in out and "not_a_real_state" in out,
        f"code={code} out={out}",
    )
    lock_file = broken / ".task" / "skill-lock.json"
    lock = json.loads(lock_file.read_text(encoding="utf-8")) if lock_file.is_file() else {}
    expect(
        "L-neg-failed-migration-does-not-commit-contract",
        lock.get("schema_version") is None and lock.get("status") == "conversion_failed",
        json.dumps(lock, ensure_ascii=False)[:300],
    )
    # 失败也不得把**本来合法的**任务推进回归死锁（TASK-001 是干净的旧任务）
    code, out = run(["transition", "TASK-001", "in_progress", "--actor", "M1"], broken)
    expect(
        "L-pos-failed-migration-does-not-deadlock-clean-task",
        "SCHEMA_REGRESSION_RISK" not in out and code == 0,
        f"code={code} out={out}",
    )

    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(broken, ignore_errors=True)
    print("ALL v0.36 MIGRATION-DEADLOCK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
