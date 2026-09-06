#!/usr/bin/env python3
"""Minimal, dependency-free task gate for multi-window_M v0.25.

v0.25 parallel collaboration and 加分 wording live in SKILL.md. G2 ignores
role-owned .task files: manifest.json, rerun.json, and verify-report.json.
Worker src/tests/worker-report/evidence must still be declared.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WINDOW_RE = re.compile(r"^(?:M[1-9]|M10|C[1-9][0-9]*)$")
TASK_STATES = {
    "pending", "in_progress", "worker_done", "verifying", "verified",
    "integrated", "done", "paused", "blocked", "reopened",
}
WINDOW_STATES = {
    "pending", "in_progress", "worker_done", "verifying", "verified",
    "paused", "blocked",
}
TRANSITIONS = {
    "pending": {"in_progress", "paused", "blocked"},
    "in_progress": {"worker_done", "paused", "blocked", "reopened"},
    "worker_done": {"verifying", "integrated", "paused", "blocked", "reopened"},
    "verifying": {"verified", "worker_done", "blocked", "reopened"},
    "verified": {"integrated", "reopened"},
    "integrated": {"done", "reopened"},
    "done": {"reopened"},
    "paused": {"in_progress", "blocked", "reopened"},
    "blocked": {"in_progress", "paused", "reopened"},
    "reopened": {"in_progress", "paused", "blocked"},
}
KNOWN_COMMANDS = {
    "init",
    "round-init",
    "receipt",
    "request-check",
    "gate",
    "audit-round",
    "audit",
    "hook-audit",
    "migrate-project",
    "status",
    "transition",
    "reopen",
}
ROOT_PLACEMENT_ERROR = (
    "FAIL: --root must come before the subcommand\n"
    "  correct: py -3 taskctl.py --root <project> <command> ...\n"
    "  wrong:   py -3 taskctl.py <command> ... --root <project>"
)
HOOK_LOG_MAX_LINES = 100
RERUN_TIMEOUT_SEC = 60
SOURCE_HOST = {
    "cursor-stop": "cursor",
    "codex-stop": "codex",
    "zcode-stop": "zcode",
    "manual": "manual",
}
COMMAND_PREFIXES = (
    "py ",
    "py.exe ",
    "python ",
    "python3 ",
    "pytest",
    "node ",
    "npm ",
    "npx ",
    "go ",
    "cargo ",
    "dotnet ",
    "pwsh ",
    "powershell ",
    "cmd ",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_project_root(start: Optional[Path] = None) -> Path:
    """Find a safe project root without inheriting a parent's .task by accident.

    A .task directory is accepted only at the requested/current directory.
    Parent traversal is limited to .git roots; projects without .git can pass
    --root explicitly.
    """
    current = (start or Path.cwd()).resolve()
    if (current / ".task").is_dir():
        return current
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    return current


def task_root(root: Path) -> Path:
    return root / ".task"


def task_dir(root: Path, task_id: str) -> Path:
    return task_root(root) / task_id


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"missing file: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}")
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def record_status(manifest: Dict[str, Any], status: str, actor: str, reason: str) -> None:
    timestamp = now_iso()
    manifest["status"] = status
    manifest.setdefault("status_history", []).append(
        {"status": status, "at": timestamp, "by": actor, "reason": reason}
    )


def require_task_id(task_id: str) -> None:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError(
            "task_id must contain only letters, digits, '.', '_' or '-'; "
            "it must not start with punctuation"
        )


def valid_window(value: Any) -> bool:
    return isinstance(value, str) and bool(WINDOW_RE.fullmatch(value))


def normalized_path(value: Any) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def gate_owned_paths(task_id: str) -> set[str]:
    return {
        normalized_path(f".task/{task_id}/manifest.json"),
        normalized_path(f".task/{task_id}/rerun.json"),
        normalized_path(f".task/{task_id}/verify-report.json"),
    }


def path_allowed(path: Any, allowed_paths: List[Any]) -> bool:
    candidate = normalized_path(path)
    for allowed in allowed_paths:
        prefix = normalized_path(allowed).rstrip("/")
        if prefix in {"", "."}:
            return True
        if candidate == prefix or candidate.startswith(prefix + "/"):
            return True
    return False


def safe_destination(root: Path, raw_path: str) -> Path:
    destination = Path(raw_path)
    if destination.is_absolute():
        raise ValueError("destination must be relative to the project root")
    resolved_root = root.resolve()
    resolved = (resolved_root / destination).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        raise ValueError("destination must stay inside the project root")
    return resolved


def load_manifest(root: Path, task_id: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    path = task_dir(root, task_id) / "manifest.json"
    if not path.exists():
        return None, [f"{task_id}: missing {path.relative_to(root)}"]
    try:
        return read_json(path), []
    except ValueError as exc:
        return None, [str(exc)]


def validate_manifest(manifest: Dict[str, Any], task_id: str) -> List[str]:
    errors: List[str] = []
    if manifest.get("task_id") != task_id:
        errors.append(f"{task_id}: manifest.task_id does not match directory")
    if not manifest.get("owner"):
        errors.append(f"{task_id}: missing owner")
    elif not valid_window(manifest["owner"]):
        errors.append(f"{task_id}: owner must be M1-M10 or Cn")

    status = manifest.get("status", "pending")
    if status not in TASK_STATES:
        errors.append(f"{task_id}: invalid status: {status}")

    allowed_paths = manifest.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not allowed_paths:
        errors.append(f"{task_id}: allowed_paths must be a non-empty list before dispatch")
    elif any(not isinstance(value, str) or not value.strip() for value in allowed_paths):
        errors.append(f"{task_id}: allowed_paths must contain non-empty strings")

    requirements = manifest.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append(f"{task_id}: requirements must be a non-empty list")
        return errors

    ids: List[str] = []
    for index, requirement in enumerate(requirements, start=1):
        if not isinstance(requirement, dict):
            errors.append(f"{task_id}: requirements[{index}] must be an object")
            continue
        req_id = requirement.get("id")
        if not isinstance(req_id, str) or not req_id.strip():
            errors.append(f"{task_id}: requirements[{index}] missing id")
        else:
            ids.append(req_id)
        if not isinstance(requirement.get("text"), str) or not requirement["text"].strip():
            errors.append(f"{task_id}: {req_id or index} missing text")
        if not isinstance(requirement.get("verify"), str) or not requirement["verify"].strip():
            errors.append(f"{task_id}: {req_id or index} missing verify")

    duplicates = sorted({req_id for req_id in ids if ids.count(req_id) > 1})
    if duplicates:
        errors.append(f"{task_id}: duplicate requirement ids: {', '.join(duplicates)}")
    return errors


def requirement_ids(manifest: Dict[str, Any]) -> List[str]:
    return [item["id"] for item in manifest.get("requirements", []) if isinstance(item, dict) and item.get("id")]


def validate_worker(root: Path, task_id: str, manifest: Dict[str, Any]) -> List[str]:
    report_path = task_dir(root, task_id) / "worker-report.json"
    errors: List[str] = []
    if not report_path.exists():
        return [f"{task_id}: missing {report_path.relative_to(root)}"]
    try:
        report = read_json(report_path)
    except ValueError as exc:
        return [str(exc)]

    if report.get("task_id") != task_id:
        errors.append(f"{task_id}: worker-report.task_id does not match")
    if report.get("status") not in {"worker_done", "review"}:
        errors.append(f"{task_id}: worker-report.status must be worker_done/review")
    if not valid_window(report.get("window")):
        errors.append(f"{task_id}: worker-report.window must be M1-M10 or Cn")

    required = set(requirement_ids(manifest))
    covered = report.get("covered_requirements")
    if not isinstance(covered, list):
        errors.append(f"{task_id}: worker-report.covered_requirements must be a list")
        covered_set: set[str] = set()
    else:
        covered_set = {str(value) for value in covered}
    missing = sorted(required - covered_set)
    unknown = sorted(covered_set - required)
    if missing:
        errors.append(f"{task_id}: uncovered requirements: {', '.join(missing)}")
    if unknown:
        errors.append(f"{task_id}: worker report references unknown requirements: {', '.join(unknown)}")

    evidence = report.get("evidence")
    evidence_ids = set()
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("requirement_id"):
                evidence_ids.add(str(item["requirement_id"]))
            elif isinstance(item, str) and item.strip():
                # A plain note is accepted as evidence text in v0.20.
                continue
    else:
        errors.append(f"{task_id}: worker-report.evidence must be a list")
    missing_evidence = sorted(required - evidence_ids)
    if missing_evidence:
        errors.append(f"{task_id}: requirements missing evidence entries: {', '.join(missing_evidence)}")

    known_gaps = report.get("known_gaps", [])
    if known_gaps:
        errors.append(f"{task_id}: worker report contains known_gaps")

    changed_files = report.get("changed_files")
    if not isinstance(changed_files, list):
        errors.append(f"{task_id}: worker-report.changed_files must be a list")
    else:
        outside = sorted(
            normalized_path(value)
            for value in changed_files
            if not path_allowed(value, manifest.get("allowed_paths", []))
        )
        if outside:
            errors.append(
                f"{task_id}: changed files outside allowed_paths: {', '.join(outside)}"
            )
    return errors


def looks_like_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if lower.startswith(COMMAND_PREFIXES):
        return True
    return stripped.startswith((".\\", "./", "scripts/", "tests/"))


def collect_verify_commands(manifest: Dict[str, Any], report: Optional[Dict[str, Any]]) -> List[str]:
    commands: List[str] = []
    for requirement in manifest.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        verify_cmd = requirement.get("verify_cmd")
        if isinstance(verify_cmd, str) and verify_cmd.strip():
            commands.append(verify_cmd.strip())
            continue
        verify = requirement.get("verify")
        if isinstance(verify, str) and looks_like_command(verify):
            commands.append(verify.strip())
    if isinstance(report, dict):
        tests = report.get("tests")
        if isinstance(tests, list):
            for item in tests:
                if isinstance(item, dict) and isinstance(item.get("command"), str) and item["command"].strip():
                    commands.append(item["command"].strip())
    unique: List[str] = []
    seen = set()
    for command in commands:
        if command not in seen:
            unique.append(command)
            seen.add(command)
    return unique


def git_changed_files(root: Path) -> Tuple[str, List[str]]:
    if not (root / ".git").exists():
        return "skipped", []
    names: List[str] = []
    queries = (
        ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
        ["git", "-C", str(root), "diff", "--name-only", "--cached"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    )
    for query in queries:
        try:
            completed = subprocess.run(
                query,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "skipped", []
        if completed.returncode != 0:
            return "skipped", []
        names.extend(
            line.strip().replace("\\", "/")
            for line in (completed.stdout or "").splitlines()
            if line.strip()
        )
    unique = list(dict.fromkeys(names))
    return "ok", unique


def run_verify_command(root: Path, command: str) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=RERUN_TIMEOUT_SEC,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "command": command,
            "exit_code": int(completed.returncode),
            "stdout_tail": stdout[-500:],
            "stderr_tail": stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": f"timeout after {RERUN_TIMEOUT_SEC}s",
        }
    except OSError as exc:
        return {
            "command": command,
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": str(exc),
        }


def validate_evidence_rerun(root: Path, task_id: str, manifest: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    report: Optional[Dict[str, Any]] = None
    report_path = task_dir(root, task_id) / "worker-report.json"
    if report_path.exists():
        try:
            report = read_json(report_path)
        except ValueError as exc:
            return [str(exc)]

    evidence_ok = True
    if isinstance(report, dict) and isinstance(report.get("evidence"), list):
        for index, item in enumerate(report["evidence"]):
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path") or item.get("file")
            if not isinstance(raw_path, str) or not raw_path.strip():
                errors.append(f"{task_id}: evidence[{index}] missing path")
                evidence_ok = False
                continue
            evidence_file = (root / raw_path).resolve()
            try:
                evidence_file.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{task_id}: evidence path escapes project root: {raw_path}")
                evidence_ok = False
                continue
            if not evidence_file.exists():
                errors.append(f"{task_id}: missing evidence file: {normalized_path(raw_path)}")
                evidence_ok = False

    claimed = []
    if isinstance(report, dict) and isinstance(report.get("changed_files"), list):
        claimed = [normalized_path(value) for value in report["changed_files"]]
    claimed_set = set(claimed)
    allowed = manifest.get("allowed_paths", [])

    git_status, diff_files = git_changed_files(root)
    if git_status == "ok":
        in_zone = [normalized_path(path) for path in diff_files if path_allowed(path, allowed)]
        owned = gate_owned_paths(task_id)
        undeclared = sorted(
            path for path in in_zone
            if path not in claimed_set and path not in owned
        )
        if undeclared:
            errors.append(
                f"{task_id}: git changes in allowed_paths not listed in changed_files: {', '.join(undeclared)}"
            )

    commands = collect_verify_commands(manifest, report)
    command_results = [run_verify_command(root, command) for command in commands]
    for result in command_results:
        if result["exit_code"] != 0:
            errors.append(
                f"{task_id}: verify command failed (exit {result['exit_code']}): {result['command']}"
            )

    write_json(
        task_dir(root, task_id) / "rerun.json",
        {
            "task_id": task_id,
            "at": now_iso(),
            "git": git_status,
            "diff_files": diff_files,
            "commands": command_results,
            "evidence_ok": evidence_ok and not any(
                "missing evidence file" in error or "missing path" in error
                for error in errors
            ),
        },
    )
    return errors


def verification_policy(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Single policy table for Gate, transition, and M1 close.

    independent_verification | close path
    false (low AND attempt < 2 AND flag unset) | worker_done -> integrated -> done
    true  (medium/high OR attempt >= 2 OR flag) | worker_done -> verifying -> verified -> integrated -> done
    """
    risk = str(manifest.get("risk", "medium")).lower()
    attempt = int(manifest.get("attempt", 0) or 0)
    flagged = bool(manifest.get("verification_required"))
    independent = flagged or risk in {"medium", "high"} or attempt >= 2
    short_close = not independent
    conflict = ""
    if independent == short_close:
        conflict = (
            "POLICY_CONFLICT: independent_verification and "
            "worker_done->integrated must be opposites"
        )
    return {
        "risk": risk,
        "attempt": attempt,
        "flagged": flagged,
        "independent_verification": independent,
        "allow_worker_done_to_integrated": short_close,
        "conflict": conflict,
    }


def verification_required(manifest: Dict[str, Any]) -> bool:
    return bool(verification_policy(manifest)["independent_verification"])


def validate_verification(root: Path, task_id: str, manifest: Dict[str, Any], required: bool) -> List[str]:
    report_path = task_dir(root, task_id) / "verify-report.json"
    if not report_path.exists():
        return [f"{task_id}: missing {report_path.relative_to(root)}"] if required else []
    try:
        report = read_json(report_path)
    except ValueError as exc:
        return [str(exc)]

    errors: List[str] = []
    if report.get("task_id") != task_id:
        errors.append(f"{task_id}: verify-report.task_id does not match")
    reviewer = report.get("reviewer")
    worker_window = ""
    worker_path = task_dir(root, task_id) / "worker-report.json"
    if worker_path.exists():
        try:
            worker_window = str(read_json(worker_path).get("window", ""))
        except ValueError:
            pass
    if not valid_window(reviewer):
        errors.append(f"{task_id}: verify-report.reviewer must be M1-M10 or Cn")
    elif worker_window and reviewer == worker_window:
        errors.append(f"{task_id}: reviewer must differ from worker window ({worker_window})")
    if str(report.get("result", "")).lower() != "pass":
        errors.append(f"{task_id}: verify-report.result is not pass")
    required_ids = set(requirement_ids(manifest))
    checked = report.get("checked_requirements")
    if not isinstance(checked, list):
        errors.append(f"{task_id}: verify-report.checked_requirements must be a list")
    checked_ids = {str(value) for value in checked} if isinstance(checked, list) else set()
    missing_checked = sorted(required_ids - checked_ids)
    if missing_checked:
        errors.append(f"{task_id}: verifier did not check: {', '.join(missing_checked)}")
    if report.get("missing"):
        errors.append(f"{task_id}: verifier reports missing items")
    return errors


def gate(root: Path, task_id: str, phase: str) -> List[str]:
    manifest, errors = load_manifest(root, task_id)
    if manifest is None:
        return errors
    policy = verification_policy(manifest)
    if policy["conflict"]:
        return [policy["conflict"]]
    errors.extend(validate_manifest(manifest, task_id))
    errors.extend(validate_worker(root, task_id, manifest))
    if phase == "full":
        errors.extend(
            validate_verification(
                root, task_id, manifest, policy["independent_verification"]
            )
        )
        errors.extend(validate_evidence_rerun(root, task_id, manifest))
    return errors


def round_path(root: Path) -> Path:
    return task_root(root) / "round.json"


def cmd_init(args: argparse.Namespace, root: Path) -> int:
    require_task_id(args.task_id)
    directory = task_dir(root, args.task_id)
    if directory.exists():
        print(f"FAIL: task already exists: {directory}")
        return 1
    directory.mkdir(parents=True)
    (directory / "evidence").mkdir()
    manifest = {
        "task_id": args.task_id,
        "title": args.title or "",
        "owner": args.owner or "M1",
        "track": args.track,
        "risk": args.risk,
        "attempt": 0,
        "status": "pending",
        "status_history": [{"status": "pending", "at": now_iso(), "by": "taskctl"}],
        "allowed_paths": [],
        "requirements": [
            {"id": "R1", "text": "请由 M1 改写为可验收需求", "verify": "请填写验证方法"}
        ],
    }
    write_json(directory / "manifest.json", manifest)
    print(f"CREATED {directory.relative_to(root)}")
    print("NEXT: edit manifest.json before dispatching the task")
    return 0


def cmd_round_init(args: argparse.Namespace, root: Path) -> int:
    path = round_path(root)
    if path.exists():
        print(f"FAIL: round already exists: {path}")
        return 1
    windows = args.windows
    invalid_windows = [window for window in windows if not valid_window(window)]
    if invalid_windows:
        print(f"FAIL: invalid window id(s): {', '.join(invalid_windows)}")
        return 1
    if len(set(windows)) != len(windows):
        print("FAIL: duplicate window ids in round")
        return 1
    temporary_windows = [window for window in windows if window.startswith("C")]
    if len(temporary_windows) > 4:
        print("FAIL: a round may contain at most 4 temporary C windows")
        return 1
    tasks: Dict[str, List[str]] = {window: [] for window in windows}
    for assignment in args.task:
        if "=" not in assignment:
            print(f"FAIL: invalid --task assignment: {assignment}; use WINDOW=TASK_ID")
            return 1
        window, task_id = assignment.split("=", 1)
        if window not in tasks or not task_id:
            print(f"FAIL: --task must use an expected window and non-empty task id: {assignment}")
            return 1
        tasks[window].append(task_id)
    write_json(
        path,
        {
            "round_id": args.round_id,
            "expected_windows": windows,
            "receipts": [],
            "window_status": {window: "pending" for window in windows},
            "tasks": tasks,
            "check_requested": False,
        },
    )
    print(f"CREATED {path.relative_to(root)}")
    return 0


def cmd_receipt(args: argparse.Namespace, root: Path) -> int:
    path = round_path(root)
    try:
        data = read_json(path)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    expected = data.get("expected_windows", [])
    if args.window not in expected:
        print(f"FAIL: {args.window} is not in expected_windows")
        return 1
    receipts = data.setdefault("receipts", [])
    if args.window not in receipts:
        receipts.append(args.window)
    data["last_receipt_at"] = now_iso()
    data.setdefault("window_status", {})[args.window] = "worker_done"
    data.setdefault("window_status_at", {})[args.window] = data["last_receipt_at"]
    write_json(path, data)
    print(f"RECEIPT RECORDED {args.window}")
    return 0


def cmd_request_check(root: Path) -> int:
    path = round_path(root)
    try:
        data = read_json(path)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    data["check_requested"] = True
    data["check_requested_at"] = now_iso()
    write_json(path, data)
    print("CHECK_REQUESTED true")
    return 0


def task_ids_for_round(data: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    tasks = data.get("tasks", {})
    if isinstance(tasks, dict):
        for values in tasks.values():
            if isinstance(values, list):
                result.extend(str(value) for value in values)
            elif isinstance(values, str):
                result.append(values)
    return list(dict.fromkeys(result))


def cmd_gate(args: argparse.Namespace, root: Path) -> int:
    phase = "basic" if args.basic else "full"
    errors = gate(root, args.task_id, phase)
    if errors:
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("RESULT PASS")
    return 0


def cmd_audit_round(root: Path) -> int:
    path = round_path(root)
    if not path.exists():
        # This is the normal result for projects that do not use this skill.
        print("AUDIT SKIP (no .task/round.json)")
        return 0
    try:
        data = read_json(path)
    except ValueError as exc:
        print(f"AUDIT FAIL: {exc}")
        return 1
    if not data.get("check_requested", False):
        print("AUDIT SKIP (check_requested=false)")
        return 0

    expected = {str(value) for value in data.get("expected_windows", [])}
    receipts = {str(value) for value in data.get("receipts", [])}
    window_status = data.get("window_status")
    if not isinstance(window_status, dict):
        print("ROUND_GATE_FAIL")
        print("- round.json: missing window_status object")
        return 1
    status_ids = {str(value) for value in window_status}
    missing_status = sorted(expected - status_ids)
    unknown_status = sorted(status_ids - expected)
    if missing_status or unknown_status:
        print("ROUND_GATE_FAIL")
        if missing_status:
            print(f"- missing window_status entries: {', '.join(missing_status)}")
        if unknown_status:
            print(f"- unknown window_status entries: {', '.join(unknown_status)}")
        return 1
    invalid_states = sorted(
        f"{window}={window_status.get(window)}"
        for window in expected
        if window_status.get(window) not in WINDOW_STATES
    )
    if invalid_states:
        print("ROUND_GATE_FAIL")
        print(f"- invalid window states: {', '.join(invalid_states)}")
        return 1
    pending_with_receipt = sorted(
        window for window in expected
        if window in receipts and window_status.get(window) in {"pending", "in_progress"}
    )
    completed_without_receipt = sorted(
        window for window in expected
        if window not in receipts and window_status.get(window) in {"worker_done", "verifying", "verified"}
    )
    if pending_with_receipt or completed_without_receipt:
        print("ROUND_GATE_FAIL")
        if pending_with_receipt:
            print(f"- receipt windows still pending: {', '.join(pending_with_receipt)}")
        if completed_without_receipt:
            print(f"- completed windows missing receipt: {', '.join(completed_without_receipt)}")
        return 1
    missing_receipts = sorted(expected - receipts)
    if missing_receipts:
        print("ROUND_NOT_READY")
        print(f"- missing receipts: {', '.join(missing_receipts)}")
        return 0

    task_ids = task_ids_for_round(data)
    if not task_ids:
        print("AUDIT FAIL: round.tasks contains no task ids")
        return 1

    all_errors: List[str] = []
    for task_id in task_ids:
        all_errors.extend(gate(root, task_id, "basic"))
    if all_errors:
        print("ROUND_GATE_FAIL")
        for error in all_errors:
            print(f"- {error}")
        return 1
    print("BASIC_GATE_PASS")
    full_errors: List[str] = []
    for task_id in task_ids:
        full_errors.extend(gate(root, task_id, "full"))
    if full_errors:
        if any(
            "verify-report" in error or "verifier" in error or "reviewer" in error
            for error in full_errors
        ):
            print("VERIFY_REQUIRED")
        print("FULL_GATE_FAIL")
        for error in full_errors:
            print(f"- {error}")
        return 1
    print("FULL_GATE_PASS")
    print("ROUND_READY_TO_CLOSE")
    return 0


def hook_log_path(root: Path) -> Path:
    return task_root(root) / "hook-runs.jsonl"


def current_round_id(root: Path) -> str:
    path = round_path(root)
    if not path.exists():
        return ""
    try:
        data = read_json(path)
    except ValueError:
        return ""
    return str(data.get("round_id") or "")


def trim_hook_log(path: Path) -> None:
    if not path.exists():
        return
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) <= HOOK_LOG_MAX_LINES:
        return
    path.write_text("\n".join(lines[-HOOK_LOG_MAX_LINES:]) + "\n", encoding="utf-8")


def append_hook_run(
    root: Path,
    *,
    run_id: str,
    phase: str,
    source: str,
    host: str,
    exit_code: Optional[int],
    audit_result: str,
) -> Path:
    path = hook_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "phase": phase,
        "timestamp": now_iso(),
        "event": "stop",
        "source": source,
        "host": host,
        "cwd": str(Path.cwd()),
        "project_root": str(root),
        "round_id": current_round_id(root),
        "exit_code": exit_code,
        "audit_result": audit_result,
        "command": "taskctl.py hook-audit",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    trim_hook_log(path)
    return path


def cmd_hook_audit(root: Path, source: str, host: Optional[str] = None) -> int:
    if not task_root(root).is_dir():
        print("HOOK_AUDIT_SKIP (no .task)")
        return 0
    run_id = str(uuid.uuid4())
    resolved_host = host or SOURCE_HOST.get(source, "unknown")
    append_hook_run(
        root,
        run_id=run_id,
        phase="start",
        source=source,
        host=resolved_host,
        exit_code=None,
        audit_result="",
    )
    exit_code = 0
    audit_result = "ok"
    try:
        exit_code = cmd_audit_round(root)
        audit_result = "ok" if exit_code == 0 else "fail"
    except Exception as exc:
        exit_code = 1
        audit_result = f"error:{exc}"
        print(f"HOOK_AUDIT_ERROR {exc}")
    path = append_hook_run(
        root,
        run_id=run_id,
        phase="end",
        source=source,
        host=resolved_host,
        exit_code=exit_code,
        audit_result=audit_result,
    )
    print(f"HOOK_RUN_RECORDED {path.relative_to(root)} run_id={run_id} host={resolved_host}")
    return exit_code


def cmd_migrate_project(args: argparse.Namespace, root: Path) -> int:
    destination = safe_destination(root, args.destination)
    source = Path(__file__).resolve()
    if destination == source:
        print("FAIL: destination is the currently running v0.21 script")
        return 1
    if destination.exists() and not args.force:
        print(f"FAIL: destination exists; add --force to replace: {destination.relative_to(root)}")
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    print(f"MIGRATED {destination.relative_to(root)} from multi-window_M-0.25")
    return 0


def load_task_statuses(root: Path) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if not manifest_file.exists():
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError:
            statuses[directory.name] = "INVALID"
            continue
        task_id = str(manifest.get("task_id", directory.name))
        statuses[task_id] = str(manifest.get("status", "pending"))
    return statuses


def window_task_annotation(window: str, round_data: Dict[str, Any], statuses: Dict[str, str]) -> str:
    raw_tasks = round_data.get("tasks", {})
    assigned: List[str] = []
    if isinstance(raw_tasks, dict):
        value = raw_tasks.get(window, [])
        if isinstance(value, list):
            assigned = [str(item) for item in value]
        elif isinstance(value, str) and value:
            assigned = [value]
    if not assigned:
        return "tasks=-"
    parts = [f"{task_id}:{statuses.get(task_id, 'MISSING')}" for task_id in assigned]
    note = "tasks=" + ",".join(parts)
    if assigned and all(statuses.get(task_id) == "done" for task_id in assigned):
        note += " note=tasks_closed"
    return note


def cmd_status(root: Path) -> int:
    if not task_root(root).is_dir():
        print("STATUS SKIP (no .task)")
        return 0
    statuses = load_task_statuses(root)
    round_file = round_path(root)
    if round_file.exists():
        try:
            round_data = read_json(round_file)
            print(f"ROUND {round_data.get('round_id', '?')}")
            window_status = round_data.get("window_status")
            if not isinstance(window_status, dict):
                print("WINDOW_STATUS: MISSING")
                window_status = {}
            for window in round_data.get("expected_windows", []):
                state = window_status.get(window, "MISSING")
                extra = window_task_annotation(window, round_data, statuses)
                print(f"WINDOW {window}: {state}  {extra}")
        except ValueError as exc:
            print(f"STATUS FAIL: {exc}")
            return 1
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if not manifest_file.exists():
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError as exc:
            print(f"TASK {directory.name}: invalid manifest ({exc})")
            continue
        task_id = str(manifest.get("task_id", directory.name))
        gate_state = "GATE_PASS" if not gate(root, task_id, "full") else "GATE_FAIL"
        print(f"TASK {directory.name}: {manifest.get('status', 'pending')} / {gate_state}")
    return 0


def cmd_transition(args: argparse.Namespace, root: Path) -> int:
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    current = str(manifest.get("status", "pending"))
    target = args.status
    if current not in TASK_STATES:
        print(f"RESULT FAIL\n- {args.task_id}: invalid current status: {current}")
        return 1
    if target not in TASK_STATES:
        print(f"RESULT FAIL\n- invalid target status: {target}")
        return 1
    if target == current:
        print(f"RESULT FAIL\n- {args.task_id}: already {target}")
        return 1
    if target not in TRANSITIONS.get(current, set()):
        print(f"RESULT FAIL\n- illegal transition: {current} -> {target}")
        return 1

    policy = verification_policy(manifest)
    if policy["conflict"]:
        print("POLICY_CONFLICT")
        print(f"- {args.task_id}: {policy['conflict']}")
        return 1

    required_actor = {
        "in_progress": "M1",
        "worker_done": "worker",
        "verifying": "verifier",
        "verified": "verifier",
        "integrated": "M1",
        "done": "M1",
        "reopened": "M1",
    }.get(target)
    if required_actor and args.actor != required_actor:
        print(f"RESULT FAIL\n- {target} requires actor={required_actor}")
        return 1

    if target in {"verified", "integrated", "done"}:
        gate_errors = gate(root, args.task_id, "full")
        if gate_errors:
            print("RESULT FAIL")
            print(f"- {args.task_id}: Full Gate must pass before {target}")
            for error in gate_errors:
                print(f"- {error}")
            return 1
    if target == "verified" and current != "verifying":
        print("RESULT FAIL\n- verified requires current status=verifying")
        return 1
    if target == "integrated":
        if current == "verified":
            pass
        elif current == "worker_done":
            if policy["independent_verification"]:
                print("RESULT FAIL")
                print(
                    f"- {args.task_id}: independent verification required "
                    f"(risk={policy['risk']}, attempt={policy['attempt']}); "
                    "use verifying -> verified before integrated"
                )
                return 1
        else:
            print(
                "RESULT FAIL\n- integrated requires current status=verified "
                "(or worker_done when independent verification is not required)"
            )
            return 1
    if target == "done" and current != "integrated":
        print("RESULT FAIL\n- done requires current status=integrated")
        return 1

    reason = args.reason or f"transition {current} -> {target}"
    record_status(manifest, target, args.actor, reason)
    write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
    print(f"STATUS_CHANGED {args.task_id}: {current} -> {target} by {args.actor}")
    return 0


def cmd_reopen(args: argparse.Namespace, root: Path) -> int:
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.actor != "M1":
        print("RESULT FAIL\n- reopened requires actor=M1")
        return 1
    record_status(manifest, "reopened", args.actor, args.reason)
    manifest["attempt"] = int(manifest.get("attempt", 0) or 0) + 1
    manifest["reopen_reason"] = args.reason
    manifest["reopened_at"] = now_iso()
    write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
    print(f"REOPENED {args.task_id}; attempt={manifest['attempt']}")
    return 0


def root_after_subcommand(tokens: List[str]) -> bool:
    sub_index = next(
        (index for index, token in enumerate(tokens) if token in KNOWN_COMMANDS),
        None,
    )
    if sub_index is None:
        return False
    return any(
        token == "--root" or token.startswith("--root=")
        for token in tokens[sub_index + 1 :]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="multi-window_M v0.25 task gate",
        epilog="Place --root before the subcommand: taskctl.py --root <dir> status",
    )
    parser.add_argument(
        "--root",
        help="explicit project root; must appear before the subcommand "
        "(taskctl.py --root <dir> status). Required for non-git nested projects",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a task skeleton")
    init.add_argument("task_id")
    init.add_argument("--owner", default="M1")
    init.add_argument("--title")
    init.add_argument("--track", default="feature")
    init.add_argument("--risk", choices=["low", "medium", "high"], default="medium")

    round_init = sub.add_parser("round-init", help="create a round skeleton")
    round_init.add_argument("round_id")
    round_init.add_argument("windows", nargs="+", help="M/C window numbers")
    round_init.add_argument(
        "--task",
        action="append",
        default=[],
        help="map a task to a window, e.g. --task M4=TASK-001",
    )

    receipt = sub.add_parser("receipt", help="record a window receipt")
    receipt.add_argument("window")

    sub.add_parser("request-check", help="request the round audit")

    gate_parser = sub.add_parser("gate", help="run a task gate")
    gate_parser.add_argument("task_id")
    gate_parser.add_argument("--basic", action="store_true", help="skip independent verification")

    sub.add_parser("audit-round", help="audit the current round if check_requested")
    sub.add_parser("audit", help="alias for audit-round")
    hook = sub.add_parser("hook-audit", help="record a Stop-hook run and audit the current round")
    hook.add_argument("--source", choices=["manual", "codex-stop", "cursor-stop", "zcode-stop"], default="manual")
    hook.add_argument("--host", choices=["cursor", "codex", "zcode", "manual"])
    migrate = sub.add_parser("migrate-project", help="copy this v0.25 taskctl into the project")
    migrate.add_argument("--destination", default="scripts/taskctl.py")
    migrate.add_argument("--force", action="store_true", help="replace an existing destination")
    sub.add_parser("status", help="show window and task states")

    transition = sub.add_parser("transition", help="change a task status through the controlled state machine")
    transition.add_argument("task_id")
    transition.add_argument("status", choices=sorted(TASK_STATES))
    transition.add_argument("--actor", choices=["M1", "worker", "verifier", "manual"], required=True)
    transition.add_argument("--reason")

    reopen = sub.add_parser("reopen", help="reopen a task and increment attempt")
    reopen.add_argument("task_id")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--actor", choices=["M1"], default="M1")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    tokens = list(argv) if argv is not None else sys.argv[1:]
    if root_after_subcommand(tokens):
        print(ROOT_PLACEMENT_ERROR)
        return 2
    parser = build_parser()
    args = parser.parse_args(tokens)
    root = find_project_root(Path(args.root).resolve() if args.root else None)
    try:
        if args.command == "init":
            return cmd_init(args, root)
        if args.command == "round-init":
            return cmd_round_init(args, root)
        if args.command == "receipt":
            return cmd_receipt(args, root)
        if args.command == "request-check":
            return cmd_request_check(root)
        if args.command == "gate":
            return cmd_gate(args, root)
        if args.command in {"audit-round", "audit"}:
            return cmd_audit_round(root)
        if args.command == "hook-audit":
            return cmd_hook_audit(root, args.source, getattr(args, "host", None))
        if args.command == "migrate-project":
            return cmd_migrate_project(args, root)
        if args.command == "status":
            return cmd_status(root)
        if args.command == "transition":
            return cmd_transition(args, root)
        if args.command == "reopen":
            return cmd_reopen(args, root)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
