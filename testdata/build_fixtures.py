"""生成 v0.35 的契约样本,并导出冻结的期望输出。

设计约束(踩过才定下来的):
  1. 样本**不能放在仓库里**。它必须自带 git root,否则 `find_project_root()` 会向上找到
     仓库自己的 `.git`,把命令全部打到仓库根上;而一旦它自带 git root,外层仓库就会把
     它记成 embedded repo / gitlink 污染上游。
     → 所以样本生成在**仓库外的临时目录**,仓库里只放"冻结的期望输出"(纯 JSON)。
  2. Windows 上 `.git/objects` 是只读文件,`shutil.rmtree` 会 PermissionError。
     → 已存在的样本一律用 `git reset --hard` + `git clean -qfdx` 重置,不删目录。
  3. 验收命令必须**不依赖第三方包**(本机没有 pytest)。
     → 用 `py -3 -m tests.test_greet`,自带 `__main__`。
  4. `__pycache__` 必须进样本的 `.gitignore`,否则跑一次验收命令就多出"未申报改动"。
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
TASKCTL = HERE.parent / "scripts" / "taskctl.py"

# 样本生成在仓库外;期望输出写回仓库。
DEST = Path(tempfile.gettempdir()) / "mw035-contract-sample"
GITDIR = Path(tempfile.gettempdir()) / "mw035-contract-sample-gitdir"
EXPECTED = HERE / "expected-v035"


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


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reset_fixture() -> bool:
    if not (DEST / ".git").exists():
        return False
    subprocess.run(["git", "-C", str(DEST), "reset", "-q", "--hard"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(DEST), "clean", "-qfdx"], check=True, capture_output=True)
    return True


def init_fixture_repo() -> None:
    if GITDIR.exists():
        shutil.rmtree(GITDIR, ignore_errors=True)
    GITDIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(GITDIR), str(DEST)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(DEST), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(DEST),
            "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=fixture",
            "commit", "-q", "-m", "fixture baseline",
        ],
        check=True,
        capture_output=True,
    )


def seed_files() -> None:
    (DEST / "src").mkdir(parents=True, exist_ok=True)
    (DEST / "tests").mkdir(exist_ok=True)
    (DEST / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (DEST / ".gitignore").write_text(".task/\n__pycache__/\n*.pyc\n", encoding="utf-8")


def baseline_source() -> None:
    (DEST / "src" / "greet.py").write_text(
        'def greet(name: str) -> str:\n    return f"hello {name}"\n', encoding="utf-8"
    )
    (DEST / "tests" / "test_greet.py").write_text(
        "from src.greet import greet\n\n\n"
        "def test_greet() -> None:\n"
        "    assert greet('x') == 'hello x'\n\n\n"
        "if __name__ == '__main__':\n"
        "    test_greet()\n"
        "    print('1 passed')\n",
        encoding="utf-8",
    )


def main() -> int:
    reused = reset_fixture()
    if reused:
        print("[setup] 复用已有样本,已重置到基线提交")
    else:
        if DEST.exists():
            shutil.rmtree(DEST, ignore_errors=True)
        seed_files()
        baseline_source()
        init_fixture_repo()
        print(f"[setup] 样本 git root 已建(gitdir 在仓库外: {GITDIR})")
    seed_files()

    # 基线之后才有改动:让 `git diff HEAD` 真的有内容,走真实校验路径。
    (DEST / "src" / "greet.py").write_text(
        'def greet(name: str) -> str:\n    return f"hello {name}"\n\n\n'
        'def greet_all(names: list[str]) -> list[str]:\n'
        '    return [greet(name) for name in names]\n',
        encoding="utf-8",
    )

    steps: list[tuple[str, list[str]]] = [
        ("init", ["init", "TASK-001", "--owner", "M2", "--title", "合规样本", "--risk", "medium"]),
        (
            "round-init",
            [
                "round-init", "ROUND-001", "M2", "C1",
                "--task", "M2=TASK-001",
                "--verifier", "TASK-001=C1",
            ],
        ),
    ]
    outputs: dict[str, str] = {}
    for label, args in steps:
        code, out = run(args, DEST)
        outputs[label] = out
        print(f"[{label}] code={code}")
        if code != 0:
            print(out)
            print(f"ABORT at {label}")
            return 1

    manifest_path = DEST / ".task" / "TASK-001" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["allowed_paths"] = ["src/", "tests/"]
    manifest["source_refs"] = [{"id": "S1", "text": "提供 greet 函数", "maps_to": ["R1"]}]
    manifest["requirements"] = [
        {
            "id": "R1",
            "text": "greet 返回 hello <name>",
            "verify": "py -3 -m tests.test_greet",
            "verify_cmd": "py -3 -m tests.test_greet",
        }
    ]
    write_json(manifest_path, manifest)
    write_json(
        DEST / ".task" / "TASK-001" / "worker-report.json",
        {
            "task_id": "TASK-001",
            "window": "M2",
            "status": "worker_done",
            "covered_requirements": ["R1"],
            "evidence": [
                {
                    "requirement_id": "R1",
                    "path": ".task/TASK-001/evidence/r1-run.txt",
                    "note": "验收命令输出",
                }
            ],
            "changed_files": ["src/greet.py", "tests/__init__.py", "tests/test_greet.py"],
            "known_gaps": [],
            "tests": [
                {"command": "py -3 -c \"import ast; ast.parse(open('src/greet.py').read())\""}
            ],
        },
    )
    (DEST / ".task" / "TASK-001" / "evidence").mkdir(parents=True, exist_ok=True)
    (DEST / ".task" / "TASK-001" / "evidence" / "r1-run.txt").write_text("1 passed\n", encoding="utf-8")
    write_json(
        DEST / ".task" / "TASK-001" / "verify-report.json",
        {
            "task_id": "TASK-001",
            "reviewer": "C1",
            "result": "pass",
            "checked_requirements": ["R1"],
            "missing": [],
        },
    )

    steps = [
        ("receipt-worker", ["receipt", "M2"]),
        ("receipt-verifier", ["receipt", "C1", "--role", "verifier"]),
        ("in_progress", ["transition", "TASK-001", "in_progress", "--actor", "M1"]),
        ("worker_done", ["transition", "TASK-001", "worker_done", "--actor", "worker"]),
        ("verifying", ["transition", "TASK-001", "verifying", "--actor", "verifier"]),
        ("verified", ["transition", "TASK-001", "verified", "--actor", "verifier"]),
        ("integrated", ["transition", "TASK-001", "integrated", "--actor", "M1"]),
        ("done", ["transition", "TASK-001", "done", "--actor", "M1"]),
        ("gate", ["gate", "TASK-001"]),
        ("request-check", ["request-check"]),
        ("audit-round", ["audit-round"]),
    ]
    for label, args in steps:
        code, out = run(args, DEST)
        outputs[label] = out
        print(f"[{label}] code={code} :: {out.strip().splitlines()[0] if out.strip() else ''}")
        if code != 0:
            print(out)
            print(f"ABORT at {label}")
            return 1

    # 导出冻结期望:两宿主应能对同一命令序列生成等价结果。
    EXPECTED.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone

    sys.path.insert(0, str(HERE.parent / "scripts"))
    import taskctl as _taskctl  # noqa: PLC0415  (只用来记录生成时的版本)

    write_json(
        EXPECTED / "expected-manifest.json",
        {
            "note": "冻结的期望产物。两宿主应能对同一命令序列生成等价结果。",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "skill_version": _taskctl.SKILL_VERSION,
            "schema_version": _taskctl.SCHEMA_VERSION,
            "expected_tokens": {
                "gate": "RESULT PASS",
                "audit_round": ["BASIC_GATE_PASS", "FULL_GATE_PASS", "ROUND_READY_TO_CLOSE"],
            },
            "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        },
    )
    print(f"\nOK: 样本生成于 {DEST}")
    print(f"    期望输出导出到 {EXPECTED.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
