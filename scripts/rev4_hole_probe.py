"""复现 Codex rev4 复核的剩余漏洞：需要独立验收但没有指派验收窗时，
任一合法窗写一份 key 匹配的报告，`adjudicate accept` 仍返回成功。
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


def check(name, ok, detail):
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


def main() -> int:
    print("=== 复现：需要独立验收但未指派验收窗时的接受漏洞 ===\n")
    root = Path(tempfile.mkdtemp(prefix="rev4-hole-"))
    run(["init", "TASK-001", "--owner", "M2", "--risk", "low"], root)
    # 先以 low + 已指派验收窗的**合法姿态**建好基线（否则 dispatch 会先被拦，
    # 后面的 adjudicate 会因为"没有基线"报错，从而伪装成"已修好"）
    wj(root / ".task" / "TASK-001" / "manifest.json", {
        "schema_version": 1, "task_id": "TASK-001", "title": "t", "owner": "M2",
        "track": "f", "risk": "low",
        "attempt": 0, "status": "pending",
        "status_history": [{"status": "pending", "at": "2026-01-01T00:00:00+00:00", "by": "taskctl"}],
        "allowed_paths": ["src/"],
        "source_refs": [{"id": "S1", "text": "原需求", "maps_to": ["R1"]}],
        "requirements": [{"id": "R1", "text": "要能跑", "verify": 'node -e "process.exit(3)"'}],
    })
    wj(root / ".task" / "round.json", {
        "schema_version": 1, "round_id": "R1", "expected_windows": ["M2", "C1"],
        "receipts": [], "window_status": {"M2": "pending", "C1": "pending"},
        "tasks": {"M2": ["TASK-001"], "C1": []},
        "verifier_assignments": {},  # low 风险短路径：本来就不指派 verifier
        "gears": {"capability": "default", "collaboration": "P"},
        "hook_supervision": False, "check_requested": False,
    })
    run(["transition", "TASK-001", "in_progress", "--actor", "M1"], root)
    code_d, out_d = run(["gate", "TASK-001", "--dispatch"], root)
    cpath = root / ".task" / "TASK-001" / "contract.json"
    check("H0a 基线已合法建立（漏洞场景的前置）",
          code_d == 0 and cpath.is_file(), f"code={code_d} out={out_d[:160]}")

    m = root / ".task" / "TASK-001" / "manifest.json"
    d = rj(m)
    d["risk"] = "medium"                       # ← 现在要求独立验收
    d["requirements"][0]["verify"] = 'node -e "process.exit(0)"'  # 放宽原标准
    wj(m, d)
    # 本轮**没有**为这个任务指派验收窗（保持空）
    rp = root / ".task" / "round.json"
    rd = rj(rp)
    rd["verifier_assignments"] = {}
    wj(rp, rd)
    diffs, _ = t.contract_diffs(root, "TASK-001", d)
    key = t.acceptance_key(diffs)
    check("H0b 本任务确实要求独立验收且本轮未指派验收窗",
          t.verification_policy(d)["independent_verification"] is True
          and not t.task_verifier_route(rd, "TASK-001"),
          json.dumps({"policy": t.verification_policy(d),
                      "assigned": t.task_verifier_route(rd, "TASK-001")}, ensure_ascii=False))
    # 任一合法窗（C1，未被指派）写一份 key 匹配的报告
    wj(root / ".task" / "TASK-001" / "verify-report.json", {
        "task_id": "TASK-001", "reviewer": "C1", "result": "pass",
        "checked_requirements": ["R1"], "missing": [],
        "contract_key": key, "standard_verdict": "stricter",
        "standard_basis": "看起来没问题",
    })
    code, out = run(["adjudicate", "TASK-001", "accept", "--reason", "accepted"], root)
    check(
        "H1 未指派验收窗时不得接受标准变化",
        code != 0,
        f"code={code} out={out[:200]}",
    )
    # accepted 作为 verdict 太宽，应被拒绝
    wj(root / ".task" / "TASK-001" / "verify-report.json", {
        "task_id": "TASK-001", "reviewer": "C1", "result": "pass",
        "checked_requirements": ["R1"], "missing": [],
        "contract_key": key, "standard_verdict": "accepted",
    })
    code2, out2 = run(["adjudicate", "TASK-001", "accept", "--reason", "accepted"], root)
    check(
        "H2 泛泛的 accepted 不得作为独立结论",
        code2 != 0,
        f"code={code2} out={out2[:200]}",
    )
    print()
    if FAILURES:
        print(f"== {len(FAILURES)} 条已复现：{'; '.join(FAILURES)} ==")
        return 1
    print("== 全部未复现 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
