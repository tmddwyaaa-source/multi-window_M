"""v0.32: migrate-project still backs up, reports, locks; version lock is 0.32."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

TASKCTL = Path(__file__).resolve().parent / "taskctl.py"


def expect(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(detail)
        raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None, script: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        ["py", "-3", str(script or TASKCTL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="v032-mig-"))
    dest = root / "scripts" / "taskctl.py"

    code, out = run(["--root", str(root), "migrate-project", "--check"], cwd=root)
    expect(
        "M-neg-check-no-lock",
        code != 0 and "MIGRATE_FAIL" in out and "skill-lock.json" in out,
        out,
    )

    code, out = run(["--root", str(root), "migrate-project", "--destination", "scripts/taskctl.py"], cwd=root)
    expect(
        "M-pos-first-copy",
        code == 0
        and "MIGRATED scripts/taskctl.py" in out.replace("\\", "/")
        and "REPORT docs/MIGRATE-REPORT.md" in out.replace("\\", "/")
        and "Do not continue feature work until MIGRATE_READY" in out
        and not any(line.strip() == "MIGRATE_READY" for line in out.splitlines()),
        out,
    )
    expect("M-pos-dest-exists", dest.is_file(), str(dest))
    lock = json.loads((root / ".task" / "skill-lock.json").read_text(encoding="utf-8"))
    expect(
        "M-pos-lock-fields",
        lock.get("skill_version") == "0.32"
        and lock.get("taskctl_version") == "0.32"
        and bool(lock.get("migrated_at"))
        and lock.get("backup") == ""
        and lock.get("status") == "copied",
        json.dumps(lock),
    )
    report = (root / "docs" / "MIGRATE-REPORT.md").read_text(encoding="utf-8")
    expect(
        "M-pos-report",
        "skill_version: 0.32" in report and "MIGRATE_READY" in report,
        report,
    )
    first_bytes = dest.read_bytes()

    code, out = run(["--root", str(root), "migrate-project", "--destination", "scripts/taskctl.py"], cwd=root)
    expect(
        "M-neg-exists-no-force",
        code != 0 and "MIGRATE_FAIL" in out and dest.read_bytes() == first_bytes,
        out,
    )

    dest.write_text("# old-project-taskctl\n", encoding="utf-8")
    old_bytes = dest.read_bytes()
    code, out = run(
        ["--root", str(root), "migrate-project", "--destination", "scripts/taskctl.py", "--force"],
        cwd=root,
    )
    expect("M-pos-force-backup-line", code == 0 and "BACKUP" in out, out)
    lock = json.loads((root / ".task" / "skill-lock.json").read_text(encoding="utf-8"))
    backup = root / str(lock.get("backup") or "")
    expect(
        "M-pos-backup-keeps-old",
        backup.is_file() and backup.read_bytes() == old_bytes and dest.read_bytes() != old_bytes,
        f"backup={backup} lock={lock}",
    )
    expect("M-pos-force-is-032", b"v0.32" in dest.read_bytes(), dest.read_text(encoding="utf-8")[:80])

    code, out = run(
        ["--root", str(root), "migrate-project", "--destination", "scripts/taskctl.py", "--force"],
        cwd=root,
        script=dest,
    )
    expect(
        "M-neg-self",
        code != 0 and "MIGRATE_FAIL" in out and "currently running" in out,
        out,
    )

    code, out = run(["--root", str(root), "migrate-project", "--check"], cwd=root)
    expect(
        "M-pos-check-ready",
        code == 0 and "MIGRATE_READY" in out and "basic: pass" in out and "negative: pass" in out,
        out,
    )
    lock = json.loads((root / ".task" / "skill-lock.json").read_text(encoding="utf-8"))
    expect(
        "M-pos-lock-ready",
        lock.get("status") == "ready"
        and lock.get("checks", {}).get("basic") == "pass"
        and lock.get("checks", {}).get("full") == "pass"
        and lock.get("checks", {}).get("hook") == "pass"
        and lock.get("checks", {}).get("negative") == "pass",
        json.dumps(lock),
    )
    report = (root / "docs" / "MIGRATE-REPORT.md").read_text(encoding="utf-8")
    expect("M-pos-report-ready", "`MIGRATE_READY`" in report and "- basic: pass" in report, report)

    code, out = run(["--root", str(root), "handoff"], cwd=root)
    expect(
        "M-pos-handoff-ready",
        code == 0 and "skill_version: 0.32" in out and "MIGRATE_CHECK_INCOMPLETE" not in out,
        out,
    )

    print("ALL v0.32 MIGRATE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
