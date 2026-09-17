"""v0.36（DSH 方向 A/B）：就绪包与读取预算。

方向 A：窗口读一份就绪包即可开工，大件只按行号区间读（少读＝少重发）。
方向 B：就绪包内置输出预算与"不要复述"纪律（少写＝少重发）。
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
OK_COMMAND = 'py -3 -c "raise SystemExit(0)"'


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def write_project(root: Path, *, read_budget=None) -> None:
    task = root / ".task" / "TASK-001"
    task.mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "设计表.md").write_text("\n".join(f"line {i}" for i in range(1, 201)) + "\n", encoding="utf-8")
    manifest = {
        "task_id": "TASK-001",
        "title": "词库批次",
        "owner": "M4",
        "track": "feature",
        "risk": "low",
        "attempt": 0,
        "status": "in_progress",
        "allowed_paths": ["data/words-batch/"],
        "requirements": [{"id": "R1", "text": "词库批次", "verify": OK_COMMAND, "verify_cmd": OK_COMMAND}],
        "source_refs": [{"id": "S1", "text": "用户原始需求", "maps_to": ["R1"]}],
    }
    if read_budget is not None:
        manifest["read_budget"] = read_budget
    (task / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / ".task" / "round.json").write_text(
        json.dumps(
            {
                "round_id": "ROUND-001",
                "expected_windows": ["M4"],
                "receipts": [],
                "window_status": {"M4": "in_progress"},
                "tasks": {"M4": ["TASK-001"]},
                "check_requested": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_read_budget(root: Path, read_budget) -> None:
    path = root / ".task" / "TASK-001" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["read_budget"] = read_budget
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="v034-packet-"))

    plain = temp / "plain"
    write_project(plain)
    code, out = run(["--root", str(plain), "packet", "TASK-001", "--role", "worker"])
    expect(
        "P-pos-packet-header",
        code == 0
        and "就绪包" in out
        and "PACKET " not in out
        and "不要复述本包内容" in out
        and "命令输出原文" in out
        and "归属确认" in out,
        out,
    )
    expect(
        "P-pos-packet-warns-missing-read-budget",
        "未声明 `read_budget`" in out and ">100 行" in out,
        out,
    )

    budget_root = temp / "budget"
    write_project(
        budget_root,
        read_budget=[
            {"path": "docs/设计表.md", "anchor": "## 2.4 升级池", "lines": "68-105", "why": "只改池"},
            {"path": "docs/GAME-SPEC.md", "lines": "full", "why": "短文件"},
        ],
    )
    code, out = run(["--root", str(budget_root), "packet", "TASK-001", "--role", "worker", "--write"])
    expect(
        "P-pos-read-budget-rendered",
        code == 0
        and "docs/设计表.md: 锚点 ## 2.4 升级池 只读 68-105" in out
        and "只改池" in out
        and "docs/GAME-SPEC.md: **允许整篇**" in out,
        out,
    )
    expect(
        "P-pos-packet-is-a-copy-not-a-source",
        "禁止手改" in out and "可再生成的派工副本" in out and "安全权限边界" in out,
        out,
    )

    anchor_root = temp / "anchor"
    write_project(anchor_root, read_budget=[{"path": "docs/设计表.md", "anchor": "  ", "lines": "1-2"}])
    code, out = run(["--root", str(anchor_root), "gate", "TASK-001"])
    expect(
        "P-neg-blank-anchor-rejected",
        code != 0 and "anchor must be a non-empty string" in out,
        out,
    )

    # 锚点存在性：只告警、不阻塞（Codex 结论：不做成 Gate）
    drift_root = temp / "drift"
    write_project(
        drift_root,
        read_budget=[
            {"path": "docs/设计表.md", "anchor": "line 42", "lines": "1-5"},
            {"path": "docs/设计表.md", "anchor": "## 不存在的章节", "lines": "1-5"},
            {"path": "docs/nowhere.md", "anchor": "x", "lines": "1-5"},
        ],
    )
    code, out = run(["--root", str(drift_root), "packet", "TASK-001", "--role", "worker", "--check-anchors"])
    expect(
        "P-pos-anchor-check-warns-only",
        code == 0 and "ANCHOR_NOT_FOUND" in out and "找不到锚点" in out,
        out,
    )
    expect(
        "P-pos-anchor-file-missing-is-separate-token",
        "ANCHOR_FILE_NOT_FOUND" in out and "文件不存在" in out,
        out,
    )
    expect(
        "P-pos-anchor-check-does-not-block-gate",
        run(["--root", str(drift_root), "gate", "TASK-001"])[0] in {0, 1},
        "gate must still run with a drifted anchor",
    )
    code, out = run(["--root", str(drift_root), "packet", "TASK-001", "--role", "worker"])
    expect(
        "P-pos-anchor-check-is-opt-in",
        code == 0 and "ANCHOR_NOT_FOUND" not in out,
        out,
    )
    loose_root = temp / "loose"
    write_project(loose_root)
    (loose_root / "docs" / "spec.md").write_text(
        "# 规格\n\n## 章节 标题\nline 42 body\n", encoding="utf-8"
    )
    write_read_budget(
        loose_root,
        [
            # 精确命中（文件里确有 "## 章节 标题"）
            {"path": "docs/spec.md", "anchor": "## 章节 标题", "lines": "1-4"},
            # 格式漂移：完整串不在，但锚点第一个有意义词 "章节" 在 → LOOSE
            {"path": "docs/spec.md", "anchor": "### 章节 标题（旧）", "lines": "1-4"},
        ],
    )
    code, out = run(["--root", str(loose_root), "packet", "TASK-001", "--role", "worker", "--check-anchors"])
    expect(
        "P-pos-anchor-loose-match",
        code == 0
        and "ANCHOR_LOOSE" in out
        and out.count("ANCHOR_NOT_FOUND") == 0,
        out,
    )
    expect(
        "P-neg-markdown-heading-not-used-as-token",
        "包含 '###'" not in out and "包含 '##'" not in out,
        out,
    )
    packet_file = budget_root / ".task" / "TASK-001" / "packet-M4.md"
    expect(
        "P-pos-packet-written-to-disk",
        packet_file.is_file()
        and "就绪包" in packet_file.read_text(encoding="utf-8")
        and "锚点 ## 2.4 升级池 只读 68-105" in packet_file.read_text(encoding="utf-8"),
        str(packet_file),
    )

    bad_root = temp / "bad"
    write_project(bad_root, read_budget=[{"path": "docs/x.md", "lines": "abc"}])
    code, out = run(["--root", str(bad_root), "gate", "TASK-001"])
    expect(
        "P-neg-bad-read-budget-rejected",
        code != 0 and "read_budget[1] lines must be" in out,
        out,
    )

    bad_shape_root = temp / "bad-shape"
    write_project(bad_shape_root, read_budget=["docs/设计表.md"])
    code, out = run(["--root", str(bad_shape_root), "gate", "TASK-001"])
    expect(
        "P-neg-read-budget-shaped-wrong",
        code != 0 and "read_budget[1] must be an object" in out,
        out,
    )

    verifier_root = temp / "verifier"
    write_project(verifier_root)
    code, out = run(["--root", str(verifier_root), "packet", "TASK-001", "--role", "verifier"])
    expect(
        "P-neg-packet-verifier-without-assignment",
        code != 0 and "BRIEF_FAIL" in out,
        out,
    )

    code, out = run(["--root", str(plain), "brief", "TASK-001", "--role", "worker"])
    expect(
        "P-pos-brief-still-works-with-redirect",
        code == 0 and "BRIEF TASK-001 role=worker" in out and "读取预算" in out,
        out,
    )

    print("ALL v0.36 PACKET CHECKS PASSED")
    print(f"temp: {temp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
