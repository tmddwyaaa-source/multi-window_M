"""独立复现 Codex 对 rev2 提出的 4 条新反例（不照抄它的脚本，自己构造）。

判定：打印 REPRODUCED / not-reproduced，并按退出码反映（累计失败 → 非 0）。
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


def task(status="pending", source_refs=None, allowed=None, verify='node -e "process.exit(3)"'):
    return {
        "schema_version": 1, "task_id": "TASK-001", "title": "t", "owner": "M2",
        "track": "f", "risk": "low", "attempt": 0, "status": status,
        "status_history": [{"status": status, "at": "2026-01-01T00:00:00+00:00", "by": "taskctl"}],
        "allowed_paths": allowed if allowed is not None else ["src/"],
        "source_refs": source_refs if source_refs is not None else [
            {"id": "S1", "text": "用户需求原文", "maps_to": ["R1"]}
        ],
        "requirements": [{"id": "R1", "text": "要能跑", "verify": verify}],
    }


def setup(tag):
    root = Path(tempfile.mkdtemp(prefix=f"rv2-{tag}-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    wj(root / ".task" / "round.json", {
        "schema_version": 1, "round_id": "R1", "expected_windows": ["M2", "M4"],
        "receipts": [], "window_status": {"M2": "pending", "M4": "pending"},
        "tasks": {"M2": ["TASK-001"], "M4": []}, "verifier_assignments": {},
        "gears": {"capability": "default", "collaboration": "P"},
        "hook_supervision": False, "check_requested": False,
    })
    return root


def n1_dispatch_without_source_mapping():
    """Q1 反例：需求与路径齐全，但 source_refs=[] → --dispatch 仍通过并建基线。"""
    root = setup("n1")
    wj(root / ".task" / "TASK-001" / "manifest.json", task(source_refs=[]))
    code, out = run(["gate", "TASK-001", "--dispatch"], root)
    created = (root / ".task" / "TASK-001" / "contract.json").is_file()
    check(
        "N1 无来源映射时 --dispatch 不应通过/建基线",
        not (code == 0 and created),
        f"code={code} contract_created={created} out={out.strip()[:200]}",
    )


def n2_accept_without_verifier_judgment():
    """Q2 反例：把 verify 从失败改成成功，没有任何验收报告，直接 adjudicate accept。"""
    root = setup("n2")
    wj(root / ".task" / "TASK-001" / "manifest.json", task(status="pending"))
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    run(["gate", "TASK-001", "--dispatch"], root)
    m = root / ".task" / "TASK-001" / "manifest.json"
    d = rj(m)
    d["requirements"][0]["verify"] = 'node -e "process.exit(0)"'  # 放宽
    wj(m, d)
    code, out = run(["adjudicate", "TASK-001", "accept", "--reason", "accepted without verifier report"], root)
    errors = t.validate_contract(root, "TASK-001", rj(m), required=True)
    check(
        "N2 无验收者判断证据时不应接受",
        not (code == 0 and errors == []),
        f"code={code} errors={errors[:1]} out={out.strip()[:200]}",
    )


def n3_same_round_reworded_counts_twice():
    """Q3 反例：同一轮、同卡点、同负责人，只改措辞 → 计数 1→2。"""
    root = setup("n3")
    wj(root / ".task" / "TASK-001" / "manifest.json", task(status="pending"))
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    m = root / ".task" / "TASK-001" / "manifest.json"
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "animation frozen", "--actor", "worker"], root)
    f1 = rj(m)["block_attempts"]["attempts"]
    run(["attempt", "TASK-001", "--block-id", "B1", "--reason", "animation is still frozen", "--actor", "worker"], root)
    f2 = rj(m)["block_attempts"]["attempts"]
    check(
        "N3 同轮改写措辞不应增加计数",
        f2 == f1,
        f"f1={f1} f2={f2}（r 未重新进入施工态，应视为同一次失败的补充说明）",
    )


def n4_baseline_deleted_after_new_dispatch():
    """Q4 反例：新版派工建了基线，随后删除 contract.json，收口应阻断而不是只提示。"""
    root = setup("n4")
    wj(root / ".task" / "TASK-001" / "manifest.json", task(status="pending"))
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    code, out = run(["gate", "TASK-001", "--dispatch"], root)
    created = (root / ".task" / "TASK-001" / "contract.json").is_file()
    (root / ".task" / "TASK-001" / "contract.json").unlink()
    errors = t.validate_contract(root, "TASK-001", rj(root / ".task" / "TASK-001" / "manifest.json"), required=True)
    check(
        "N4 新版派工后基线丢失应在收口阻断",
        not (code == 0 and created and errors == []),
        f"dispatch_code={code} created={created} errors={errors[:1]}",
    )


def main() -> int:
    print("=== 独立复现 Codex rev2 的 4 条新反例 ===\n")
    n1_dispatch_without_source_mapping()
    n2_accept_without_verifier_judgment()
    n3_same_round_reworded_counts_twice()
    n4_baseline_deleted_after_new_dispatch()
    print()
    if FAILURES:
        print(f"== {len(FAILURES)} 条**已复现**（即当前实现仍有缺陷）：{', '.join(FAILURES)} ==")
        return 1
    print("== 4 条均未复现 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
