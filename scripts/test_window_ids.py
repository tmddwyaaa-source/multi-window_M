"""v0.32: window ids stay WINDOW_RE; round-init still caps temporary C at 4."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(__file__).resolve().parent / "taskctl.py"
SKILL_ROOT = TASKCTL.parent.parent


def load_taskctl():
    import importlib.util

    spec = importlib.util.spec_from_file_location("taskctl_v032", TASKCTL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        ["py", "-3", str(TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main() -> int:
    module = load_taskctl()
    expect(
        "W-src-window-re-unchanged",
        module.WINDOW_RE.pattern == r"^(?:M[1-9]|M10|C[1-9][0-9]*)$",
        module.WINDOW_RE.pattern,
    )
    expect("W-src-version-032", module.SKILL_VERSION == "0.32", module.SKILL_VERSION)

    for value in ("M1", "M10", "C1", "C4", "C99"):
        expect(f"W-pos-valid-{value}", module.valid_window(value) is True, value)
    for value in ("M11", "F2", "窗口 8", "M0", "C0", "m1", "C01", "CB1"):
        expect(f"W-neg-valid-{value}", module.valid_window(value) is False, value)

    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    templates = (SKILL_ROOT / "templates.md").read_text(encoding="utf-8")
    expect(
        "W-doc-authoritative-section",
        "## 窗口身份（窗号）" in skill and "WINDOW_RE" in skill,
        skill[:400],
    )
    for token in ("CB1", "CB2", "CBn"):
        expect(f"W-doc-skill-no-{token}", token not in skill, token)
        expect(f"W-doc-templates-no-{token}", token not in templates, token)
    expect(
        "W-doc-templates-ordinary-filename",
        "batch-02.json" in templates and "/multi-window_M-0.32" in templates,
        templates[:200],
    )

    identity = skill.split("## 窗口身份（窗号）", 1)[1]
    after_identity = identity.split("\n## ", 1)[1] if "\n## " in identity else ""
    for token in ("M11", "F2", "窗口 8"):
        expect(
            f"W-doc-ban-{token}-only-in-identity",
            token in identity and token not in after_identity,
            f"after={after_identity[:200]}",
        )

    root = Path(tempfile.mkdtemp(prefix="v032-win-"))
    code, out = run(
        ["--root", str(root), "round-init", "R-ok", "M1", "C1", "C2", "C3", "C4"],
        cwd=root,
    )
    expect(
        "W-pos-round-four-c",
        code == 0 and (root / ".task" / "round.json").is_file(),
        out,
    )

    root_five = Path(tempfile.mkdtemp(prefix="v032-win5-"))
    code, out = run(
        ["--root", str(root_five), "round-init", "R-five", "C1", "C2", "C3", "C4", "C5"],
        cwd=root_five,
    )
    expect(
        "W-neg-round-five-c",
        code != 0
        and "at most 4 temporary C" in out
        and not (root_five / ".task" / "round.json").exists(),
        out,
    )

    root_m11 = Path(tempfile.mkdtemp(prefix="v032-m11-"))
    code, out = run(
        ["--root", str(root_m11), "round-init", "R-m11", "M11"],
        cwd=root_m11,
    )
    expect(
        "W-neg-round-m11",
        code != 0 and "invalid window id" in out,
        out,
    )

    print("ALL v0.32 WINDOW ID CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
