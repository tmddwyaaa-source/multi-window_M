#!/usr/bin/env python3
"""Minimal, dependency-free task gate for multi-window_M v0.31.

Core behavior is host-agnostic. migrate-project backs up, writes a report and
skill-lock, then copies. Feature work waits for migrate-project --check
(MIGRATE_READY). Hook still does not mutate status.
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
    "selftest",
    "brief",
    "handoff",
}
BRIEF_ROLES = {"worker", "scout", "verifier"}
ROOT_PLACEMENT_ERROR = (
    "FAIL: --root must come before the subcommand\n"
    "  correct: py -3 taskctl.py --root <project> <command> ...\n"
    "  wrong:   py -3 taskctl.py <command> ... --root <project>"
)
GENERATED_STATUS_REL = "docs/TASK-STATUS.md"
GENERATED_STATUS_MARKER = "<!-- taskctl:generated-status; do not edit -->"
HOOK_LOG_MAX_LINES = 100
RERUN_TIMEOUT_SEC = 60
SKILL_VERSION = "0.31"
LOCK_REL = ".task/skill-lock.json"
MIGRATE_REPORT_REL = "docs/MIGRATE-REPORT.md"
SOURCE_HOST = {
    "cursor-stop": "cursor",
    "codex-stop": "codex",
    "zcode-stop": "zcode",
    "manual": "manual",
}
STOP_HOOK_SOURCES = {"cursor-stop", "codex-stop", "zcode-stop"}
GEAR_CAPABILITY = {"default", "bonus"}
GEAR_COLLABORATION = {"P", "A"}
SUBAGENT_ROLES = {"worker", "scout", "verifier"}
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
    errors.extend(validate_requirement_coverage(manifest, task_id))
    return errors


def requirement_ids(manifest: Dict[str, Any]) -> List[str]:
    return [item["id"] for item in manifest.get("requirements", []) if isinstance(item, dict) and item.get("id")]


def validate_requirement_coverage(manifest: Dict[str, Any], task_id: str) -> List[str]:
    """Every original ask maps to an R item; every R item maps back to a source."""
    errors: List[str] = []
    req_ids = {str(item) for item in requirement_ids(manifest)}
    refs = manifest.get("source_refs")
    if refs is None:
        return [f"REQUIREMENT_COVERAGE_FAIL: {task_id}: missing source_refs"]
    if not isinstance(refs, list) or not refs:
        return [f"REQUIREMENT_COVERAGE_FAIL: {task_id}: source_refs must be a non-empty list"]
    mapped: set[str] = set()
    seen_source: List[str] = []
    for index, item in enumerate(refs, start=1):
        if not isinstance(item, dict):
            errors.append(
                f"REQUIREMENT_COVERAGE_FAIL: {task_id}: source_refs[{index}] must be an object"
            )
            continue
        source_id = item.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"REQUIREMENT_COVERAGE_FAIL: {task_id}: source_refs[{index}] missing id")
            continue
        seen_source.append(source_id)
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"REQUIREMENT_COVERAGE_FAIL: {task_id}: {source_id} missing text")
        maps_to = item.get("maps_to")
        if not isinstance(maps_to, list) or not maps_to:
            errors.append(
                f"REQUIREMENT_COVERAGE_FAIL: {task_id}: {source_id} has no mapped R item"
            )
            continue
        for raw_id in maps_to:
            req_id = str(raw_id)
            if req_id not in req_ids:
                errors.append(
                    f"REQUIREMENT_COVERAGE_FAIL: {task_id}: {source_id} maps to missing {req_id}"
                )
            else:
                mapped.add(req_id)
    duplicates = sorted({item for item in seen_source if seen_source.count(item) > 1})
    if duplicates:
        errors.append(
            f"REQUIREMENT_COVERAGE_FAIL: {task_id}: duplicate source ids: {', '.join(duplicates)}"
        )
    unmapped_r = sorted(req_ids - mapped)
    if unmapped_r:
        errors.append(
            "REQUIREMENT_COVERAGE_FAIL: "
            f"{task_id}: R items not mapped from source_refs: {', '.join(unmapped_r)}"
        )
    return errors


def round_source_ids(round_data: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    raw = round_data.get("source_requirements")
    if raw is None:
        return [], []
    if not isinstance(raw, list) or not raw:
        return [], ["REQUIREMENT_COVERAGE_FAIL: round.source_requirements must be a non-empty list when present"]
    declared: List[str] = []
    errors: List[str] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str) and item.strip():
            declared.append(item.strip())
            continue
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            declared.append(item["id"].strip())
            continue
        errors.append(
            f"REQUIREMENT_COVERAGE_FAIL: round.source_requirements[{index}] needs id"
        )
    return declared, errors


def validate_round_source_coverage(root: Path, round_data: Dict[str, Any]) -> List[str]:
    declared, errors = round_source_ids(round_data)
    errors = list(errors)
    if errors or not declared:
        return errors
    found: set[str] = set()
    for task_id in task_ids_for_round(round_data):
        manifest, load_errors = load_manifest(root, task_id)
        if manifest is None:
            errors.extend(
                f"REQUIREMENT_COVERAGE_FAIL: {item}" if "REQUIREMENT_COVERAGE_FAIL" not in item else item
                for item in load_errors
            )
            continue
        refs = manifest.get("source_refs")
        if not isinstance(refs, list):
            continue
        for item in refs:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                found.add(item["id"].strip())
    missing = [source_id for source_id in declared if source_id not in found]
    if missing:
        errors.append(
            "REQUIREMENT_COVERAGE_FAIL: unmapped round sources: " + ", ".join(missing)
        )
    return errors


def default_gears() -> Dict[str, Any]:
    return {
        "capability": "default",
        "collaboration": "P",
        "capability_confirmed": False,
    }


def parse_gears(round_data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    raw = round_data.get("gears")
    if raw is None:
        return default_gears(), []
    if not isinstance(raw, dict):
        return default_gears(), ["GEAR_VIOLATION: gears must be an object"]
    gears = default_gears()
    if "capability" in raw:
        gears["capability"] = raw.get("capability")
    if "collaboration" in raw:
        gears["collaboration"] = raw.get("collaboration")
    if "capability_confirmed" in raw:
        gears["capability_confirmed"] = bool(raw.get("capability_confirmed"))
    return gears, []


def validate_gears(round_data: Dict[str, Any]) -> List[str]:
    gears, errors = parse_gears(round_data)
    errors = list(errors)
    capability = gears.get("capability")
    collaboration = gears.get("collaboration")
    confirmed = bool(gears.get("capability_confirmed"))
    if capability not in GEAR_CAPABILITY:
        errors.append("GEAR_VIOLATION: capability must be default|bonus")
    if collaboration not in GEAR_COLLABORATION:
        errors.append("GEAR_VIOLATION: collaboration must be P|A")
    if capability == "bonus" and not confirmed:
        errors.append(
            "GEAR_VIOLATION: bonus capability requires capability_confirmed=true"
        )
    if collaboration == "A" and not (capability == "bonus" and confirmed):
        errors.append(
            "GEAR_VIOLATION: collaboration A requires confirmed bonus capability"
        )
    return errors


def format_gears_line(round_data: Dict[str, Any]) -> str:
    gears, _errors = parse_gears(round_data)
    confirmed = "yes" if gears.get("capability_confirmed") else "no"
    hook = "yes" if round_data.get("hook_supervision") else "no"
    return (
        f"capability={gears.get('capability')} "
        f"collaboration={gears.get('collaboration')} "
        f"confirmed={confirmed} hook_supervision={hook}"
    )


def paths_overlap(left: str, right: str) -> bool:
    a = normalized_path(left).rstrip("/")
    b = normalized_path(right).rstrip("/")
    if not a or not b:
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validate_subagents(root: Path, round_data: Dict[str, Any]) -> List[str]:
    raw = round_data.get("subagents")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["PARALLEL_FAIL: subagents must be a list when present"]
    errors: List[str] = []
    seen_run: List[str] = []
    seen_slot: List[str] = []
    worker_slots: set[str] = set()
    verifier_slots: set[str] = set()
    owned: List[Tuple[str, List[str]]] = []
    for index, item in enumerate(raw, start=1):
        prefix = f"PARALLEL_FAIL: subagents[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        run_id = item.get("run_id")
        task_id = item.get("task_id")
        window = item.get("window")
        role = item.get("role")
        allowed = item.get("allowed_paths")
        if not isinstance(run_id, str) or not run_id.strip():
            errors.append(f"{prefix} missing run_id")
            continue
        if run_id in seen_run:
            errors.append(f"PARALLEL_FAIL: duplicate subagent run_id {run_id}")
        seen_run.append(run_id)
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            errors.append(f"{prefix} missing task_id")
            continue
        if not valid_window(window):
            errors.append(f"{prefix} window must be M1-M10 or Cn")
            continue
        if role not in SUBAGENT_ROLES:
            errors.append(f"{prefix} role must be worker|scout|verifier")
            continue
        if not isinstance(allowed, list) or not allowed:
            errors.append(f"{prefix} allowed_paths must be a non-empty list")
            continue
        slot = f"{task_id}/{window}/{role}"
        if slot in seen_slot:
            errors.append(f"PARALLEL_FAIL: duplicate subagent {slot}")
        seen_slot.append(slot)
        if role == "worker":
            worker_slots.add(f"{task_id}/{window}")
        if role == "verifier":
            verifier_slots.add(f"{task_id}/{window}")
        manifest, load_errors = load_manifest(root, task_id)
        if manifest is None:
            errors.extend(f"PARALLEL_FAIL: {msg}" for msg in load_errors)
            continue
        task_allowed = manifest.get("allowed_paths")
        if not isinstance(task_allowed, list):
            task_allowed = []
        agent_paths: List[str] = []
        for path in allowed:
            if not isinstance(path, str) or not path.strip():
                errors.append(f"{prefix} allowed_paths must contain non-empty strings")
                continue
            agent_paths.append(normalized_path(path))
            if not path_allowed(path, task_allowed):
                errors.append(
                    f"PARALLEL_FAIL: {run_id} path {normalized_path(path)} "
                    f"outside {task_id} allowed_paths"
                )
        owned.append((run_id, agent_paths))
    both = sorted(worker_slots & verifier_slots)
    if both:
        errors.append(
            "PARALLEL_FAIL: worker cannot also be verifier: " + ", ".join(both)
        )
    for index, (left_id, left_paths) in enumerate(owned):
        for right_id, right_paths in owned[index + 1 :]:
            if left_id == right_id:
                continue
            for left in left_paths:
                if any(paths_overlap(left, right) for right in right_paths):
                    errors.append(
                        f"PARALLEL_FAIL: concurrent paths {left_id} and {right_id}"
                    )
                    break
    return errors


def load_hook_entries(root: Path) -> List[Dict[str, Any]]:
    path = hook_log_path(root)
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def complete_stop_pairs(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in entries:
        run_id = str(item.get("run_id") or "")
        phase = str(item.get("phase") or "")
        if not run_id or phase not in {"start", "end"}:
            continue
        grouped.setdefault(run_id, {})[phase] = item
    return [
        pair["end"]
        for pair in grouped.values()
        if "start" in pair and "end" in pair
    ]


def validate_hook_evidence(root: Path, round_data: Dict[str, Any]) -> List[str]:
    if not round_data.get("hook_supervision"):
        return []
    entries = load_hook_entries(root)
    ends = complete_stop_pairs(entries)
    host_ends = [
        item for item in ends if str(item.get("source") or "") in STOP_HOOK_SOURCES
    ]
    if host_ends:
        return []
    if not hook_log_path(root).exists() or not entries:
        return [
            "HOOK_EVIDENCE_MISSING: hook_supervision=true but no hook-runs.jsonl"
        ]
    if ends and all(str(item.get("source") or "") == "manual" for item in ends):
        return [
            "HOOK_EVIDENCE_MISSING: manual audit-round/hook-audit is not hook evidence"
        ]
    return [
        "HOOK_EVIDENCE_MISSING: no complete start/end pair from a host stop hook"
    ]


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
    """Single policy table for Gate, audit-round, transition, and M1 close.

    independent_verification | unique close path
    false (low AND attempt < 2 AND flag unset) | worker_done -> integrated -> done
    true  (medium/high OR attempt >= 2 OR flag) | worker_done -> verifying -> verified -> integrated -> done
    """
    risk = str(manifest.get("risk", "medium")).lower()
    attempt = int(manifest.get("attempt", 0) or 0)
    flagged = bool(manifest.get("verification_required"))
    conflict = ""
    if risk not in {"low", "medium", "high"}:
        conflict = (
            "POLICY_CONFLICT: risk must be low|medium|high; "
            "cannot choose a unique close path"
        )
    independent = flagged or risk in {"medium", "high"} or attempt >= 2
    short_close = not independent
    explicit = manifest.get("independent_verification")
    if conflict == "" and explicit is not None and bool(explicit) != independent:
        conflict = (
            "POLICY_CONFLICT: independent_verification disagrees with "
            "risk/attempt/verification_required"
        )
    if conflict == "" and independent == short_close:
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


def emit_policy_conflict(errors: List[str]) -> None:
    if any("POLICY_CONFLICT" in str(error) for error in errors):
        print("POLICY_CONFLICT")


def emit_requirement_coverage(errors: List[str]) -> None:
    if any("REQUIREMENT_COVERAGE_FAIL" in str(error) for error in errors):
        print("REQUIREMENT_COVERAGE_FAIL")


def emit_gear_violation(errors: List[str]) -> None:
    if any("GEAR_VIOLATION" in str(error) for error in errors):
        print("GEAR_VIOLATION")


def emit_hook_evidence(errors: List[str]) -> None:
    if any("HOOK_EVIDENCE_MISSING" in str(error) for error in errors):
        print("HOOK_EVIDENCE_MISSING")


def emit_parallel_fail(errors: List[str]) -> None:
    if any("PARALLEL_FAIL" in str(error) for error in errors):
        print("PARALLEL_FAIL")


def emit_close_tokens(errors: List[str]) -> None:
    emit_policy_conflict(errors)
    emit_requirement_coverage(errors)
    emit_gear_violation(errors)
    emit_hook_evidence(errors)
    emit_parallel_fail(errors)


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
        "source_refs": [
            {"id": "S1", "text": "请由 M1 填入用户原始需求", "maps_to": ["R1"]}
        ],
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
            "gears": default_gears(),
            "hook_supervision": False,
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
        emit_policy_conflict(errors)
        emit_requirement_coverage(errors)
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("RESULT PASS")
    return 0


def cmd_audit_round(root: Path, require_hook_evidence: bool = True) -> int:
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

    coverage_errors: List[str] = []
    coverage_errors.extend(validate_gears(data))
    coverage_errors.extend(validate_subagents(root, data))
    coverage_errors.extend(validate_round_source_coverage(root, data))
    for task_id in task_ids:
        manifest, load_errors = load_manifest(root, task_id)
        if manifest is None:
            coverage_errors.extend(load_errors)
            continue
        coverage_errors.extend(validate_requirement_coverage(manifest, task_id))
    if coverage_errors:
        emit_close_tokens(coverage_errors)
        for error in coverage_errors:
            print(f"- {error}")
        return 1

    all_errors: List[str] = []
    for task_id in task_ids:
        all_errors.extend(gate(root, task_id, "basic"))
    if all_errors:
        emit_close_tokens(all_errors)
        print("ROUND_GATE_FAIL")
        for error in all_errors:
            print(f"- {error}")
        return 1
    print("BASIC_GATE_PASS")
    full_errors: List[str] = []
    for task_id in task_ids:
        full_errors.extend(gate(root, task_id, "full"))
    if full_errors:
        emit_close_tokens(full_errors)
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
    if require_hook_evidence:
        hook_errors = validate_hook_evidence(root, data)
        if hook_errors:
            emit_hook_evidence(hook_errors)
            for error in hook_errors:
                print(f"- {error}")
            return 1
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
        exit_code = cmd_audit_round(root, require_hook_evidence=False)
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
    if getattr(args, "check", False):
        return cmd_migrate_check(root)
    destination = safe_destination(root, args.destination)
    source = Path(__file__).resolve()
    if destination == source:
        print(f"MIGRATE_FAIL: destination is the currently running v{SKILL_VERSION} script")
        return 1
    dest_existed = destination.exists()
    if dest_existed and not args.force:
        print(
            "MIGRATE_FAIL: destination exists; add --force to replace "
            f"(backup is written first): {destination.relative_to(root)}"
        )
        return 1

    backup_rel = ""
    if dest_existed:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = task_root(root) / "migrate-backups" / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / destination.name
        shutil.copyfile(destination, backup_file)
        backup_rel = str(backup_file.relative_to(root)).replace("\\", "/")
        print(f"BACKUP {backup_rel}")

    dest_rel = str(destination.relative_to(root)).replace("\\", "/")
    lock: Dict[str, Any] = {
        "skill_version": SKILL_VERSION,
        "taskctl_version": SKILL_VERSION,
        "migrated_at": now_iso(),
        "source": str(source),
        "destination": dest_rel,
        "backup": backup_rel,
        "checks": {},
        "status": "planned",
    }
    write_migrate_report(root, lock)
    print(f"REPORT {MIGRATE_REPORT_REL}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    task_root(root).mkdir(parents=True, exist_ok=True)
    lock["status"] = "copied"
    write_json(lock_path(root), lock)
    write_migrate_report(root, lock)
    print(f"MIGRATED {dest_rel} from multi-window_M v{SKILL_VERSION}")
    print("NEXT: py -3 scripts/taskctl.py --root <project> migrate-project --check")
    print("Do not continue feature work until MIGRATE_READY")
    return 0


def lock_path(root: Path) -> Path:
    return task_root(root) / "skill-lock.json"


def write_migrate_report(root: Path, lock: Dict[str, Any]) -> Path:
    path = root / MIGRATE_REPORT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    checks = lock.get("checks") or {}
    lines = [
        "# 迁移报告",
        "",
        f"- 时间: {lock.get('migrated_at') or now_iso()}",
        f"- skill_version: {lock.get('skill_version')}",
        f"- taskctl_version: {lock.get('taskctl_version')}",
        f"- source: {lock.get('source')}",
        f"- destination: {lock.get('destination')}",
        f"- backup: {lock.get('backup') or '(none — first copy)'}",
        f"- status: {lock.get('status')}",
        "",
        "## 四项检查",
        "",
    ]
    if not checks:
        lines.extend(
            [
                "尚未运行。下一步：",
                "",
                "```text",
                "py -3 scripts/taskctl.py --root <项目根> migrate-project --check",
                "```",
                "",
                "未出现 `MIGRATE_READY` 前不要继续开发新功能。",
                "",
            ]
        )
    else:
        for key in ("basic", "full", "hook", "negative"):
            lines.append(f"- {key}: {checks.get(key, 'missing')}")
        if lock.get("checked_at"):
            lines.append(f"- checked_at: {lock.get('checked_at')}")
        lines.append("")
        if lock.get("status") == "ready":
            lines.append("`MIGRATE_READY`。可以继续开发。")
        else:
            lines.append("`MIGRATE_CHECK_FAIL`。修好后再 `--check`，不要继续开发。")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_copied_taskctl(dest: Path, args: List[str], cwd: Path) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(dest), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def first_task_id(root: Path) -> str:
    for directory in sorted(task_root(root).glob("TASK-*")):
        if (directory / "manifest.json").exists():
            return directory.name
    return ""


def cmd_migrate_check(root: Path) -> int:
    path = lock_path(root)
    if not path.exists():
        print("MIGRATE_FAIL: no .task/skill-lock.json; run migrate-project first")
        return 1
    try:
        lock = read_json(path)
    except ValueError as exc:
        print(f"MIGRATE_FAIL: {exc}")
        return 1
    dest_rel = str(lock.get("destination") or "scripts/taskctl.py")
    destination = safe_destination(root, dest_rel)
    if not destination.exists():
        print(f"MIGRATE_FAIL: migrated destination missing: {dest_rel}")
        return 1

    checks: Dict[str, str] = {}
    dest_text = destination.read_text(encoding="utf-8", errors="replace")
    version_ok = f"v{SKILL_VERSION}" in dest_text and str(lock.get("skill_version")) == SKILL_VERSION
    checks["basic"] = "pass" if version_ok else "fail"

    task_id = first_task_id(root)
    if not task_id:
        checks["full"] = "pass"
    else:
        code, out = run_copied_taskctl(
            destination, ["--root", str(root), "gate", task_id], cwd=root
        )
        runnable = code in {0, 1} and (
            "RESULT" in out or "POLICY_CONFLICT" in out or "FAIL" in out
        )
        checks["full"] = "pass" if runnable else "fail"

    before = load_task_statuses(root)
    hook_code, hook_out = run_copied_taskctl(
        destination,
        ["--root", str(root), "hook-audit", "--source", "manual"],
        cwd=root,
    )
    after = load_task_statuses(root)
    hook_ok = (
        hook_code in {0, 1}
        and ("HOOK_RUN_RECORDED" in hook_out or "HOOK_AUDIT_SKIP" in hook_out)
        and before == after
    )
    checks["hook"] = "pass" if hook_ok else "fail"

    place_code, place_out = run_copied_taskctl(
        destination, ["status", "--root", str(root)], cwd=root
    )
    no_force_code, no_force_out = run_copied_taskctl(
        destination,
        ["--root", str(root), "migrate-project", "--destination", dest_rel],
        cwd=root,
    )
    negative_ok = (
        place_code != 0
        and "must come before" in place_out
        and no_force_code != 0
        and "MIGRATE_FAIL" in no_force_out
    )
    checks["negative"] = "pass" if negative_ok else "fail"

    lock["checks"] = checks
    lock["checked_at"] = now_iso()
    ready = all(checks.get(key) == "pass" for key in ("basic", "full", "hook", "negative"))
    lock["status"] = "ready" if ready else "check_failed"
    write_json(path, lock)
    write_migrate_report(root, lock)
    if ready:
        print("MIGRATE_READY")
        for key in ("basic", "full", "hook", "negative"):
            print(f"- {key}: pass")
        return 0
    print("MIGRATE_CHECK_FAIL")
    for key in ("basic", "full", "hook", "negative"):
        print(f"- {key}: {checks.get(key, 'missing')}")
    return 1


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
    assigned = tasks_for_window(round_data, window)
    if not assigned:
        return "tasks=-"
    parts = [f"{task_id}:{statuses.get(task_id, 'MISSING')}" for task_id in assigned]
    note = "tasks=" + ",".join(parts)
    if assigned and all(statuses.get(task_id) == "done" for task_id in assigned):
        note += " note=tasks_closed"
    return note


def tasks_for_window(round_data: Dict[str, Any], window: str) -> List[str]:
    raw_tasks = round_data.get("tasks", {})
    if not isinstance(raw_tasks, dict):
        return []
    value = raw_tasks.get(window, [])
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def generated_status_path(root: Path) -> Path:
    return root / GENERATED_STATUS_REL


def load_manifest_map(root: Path) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if not manifest_file.exists():
            mapping[directory.name] = {"task_id": directory.name, "status": "MISSING", "owner": "-", "attempt": 0, "title": ""}
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError:
            mapping[directory.name] = {"task_id": directory.name, "status": "INVALID", "owner": "-", "attempt": 0, "title": ""}
            continue
        task_id = str(manifest.get("task_id", directory.name))
        mapping[task_id] = manifest
    return mapping


def render_status_markdown(root: Path) -> str:
    statuses = load_task_statuses(root)
    manifests = load_manifest_map(root)
    round_file = round_path(root)
    round_data: Optional[Dict[str, Any]] = None
    if round_file.exists():
        round_data = read_json(round_file)
    lines = [
        GENERATED_STATUS_MARKER,
        "# 任务状态视图",
        "",
        f"生成时间：{now_iso()}",
        "权威存储：`.task/`（`manifest.json` + `round.json`）。",
        "**禁止手改本文件。** 更新请重新运行 `taskctl.py status --markdown --write`。",
        "Registry / RECEIPT-LOG 里的 pending/done 若与本表不一致，**以本表为准**。",
        "",
    ]
    if round_data is None:
        lines.extend(["## 本轮", "", "- 无 round.json", ""])
        windows: List[str] = []
        receipts: List[str] = []
        window_status: Dict[str, Any] = {}
    else:
        windows = [str(item) for item in (round_data.get("expected_windows") or [])]
        receipts = [str(item) for item in (round_data.get("receipts") or [])]
        raw_ws = round_data.get("window_status")
        window_status = raw_ws if isinstance(raw_ws, dict) else {}
        lines.extend(
            [
                "## 本轮",
                "",
                f"- round_id: {round_data.get('round_id') or '?'}",
                f"- 本轮窗口: {', '.join(windows) or '(none)'}",
                f"- receipts: {', '.join(receipts) or '(none)'}",
                f"- check_requested: {round_data.get('check_requested', False)}",
                f"- gears: {format_gears_line(round_data)}",
                "",
            ]
        )
    lines.extend(
        [
            "## 窗口 / 任务状态表",
            "",
            "| 窗号 | 窗口状态 | receipt | 任务 | 任务状态 | 说明 |",
            "|------|----------|---------|------|----------|------|",
        ]
    )
    listed_tasks: List[str] = []
    if windows:
        for window in windows:
            assigned = tasks_for_window(round_data or {}, window)
            receipt = "yes" if window in receipts else "no"
            win_state = str(window_status.get(window, "MISSING"))
            if not assigned:
                lines.append(f"| {window} | {win_state} | {receipt} | — | — | 未绑定任务 |")
                continue
            for task_id in assigned:
                listed_tasks.append(task_id)
                task_state = statuses.get(task_id, "MISSING")
                note = "tasks_closed" if task_state == "done" else ""
                lines.append(
                    f"| {window} | {win_state} | {receipt} | {task_id} | {task_state} | {note} |"
                )
    else:
        lines.append("| — | — | — | — | — | 无本轮窗口 |")
    lines.extend(["", "## RECEIPT 摘要", ""])
    if not windows:
        lines.append("无 round.json，无法列出 receipt。")
    else:
        lines.extend(
            [
                "| 窗号 | 已 receipt | 窗口状态 | 对应任务终态 |",
                "|------|------------|----------|--------------|",
            ]
        )
        for window in windows:
            assigned = tasks_for_window(round_data or {}, window)
            task_col = ", ".join(
                f"{task_id}:{statuses.get(task_id, 'MISSING')}" for task_id in assigned
            ) or "—"
            lines.append(
                f"| {window} | {'yes' if window in receipts else 'no'} | "
                f"{window_status.get(window, 'MISSING')} | {task_col} |"
            )
    lines.extend(["", "## 全部任务", ""])
    if not manifests:
        lines.append("- 无 TASK-*")
    else:
        lines.extend(
            [
                "| 任务 | owner | 标题 | 状态 | attempt |",
                "|------|-------|------|------|---------|",
            ]
        )
        for task_id, manifest in manifests.items():
            lines.append(
                f"| {task_id} | {manifest.get('owner', '-')} | "
                f"{manifest.get('title') or '—'} | {manifest.get('status', 'pending')} | "
                f"{manifest.get('attempt', 0)} |"
            )
    unlisted = [task_id for task_id in manifests if task_id not in listed_tasks]
    if unlisted:
        lines.extend(["", "未列入本轮但存在的任务：" + ", ".join(unlisted)])
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_status(root: Path, markdown: bool = False, write: bool = False) -> int:
    if write:
        markdown = True
    if not task_root(root).is_dir():
        if markdown:
            print("STATUS_FAIL: no .task")
            return 1
        print("STATUS SKIP (no .task)")
        return 0
    if markdown:
        try:
            text = render_status_markdown(root)
        except ValueError as exc:
            print(f"STATUS FAIL: {exc}")
            return 1
        print(text, end="")
        if write:
            path = generated_status_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"STATUS_WRITTEN {GENERATED_STATUS_REL}")
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


def load_round(root: Path) -> Optional[Dict[str, Any]]:
    path = round_path(root)
    if not path.exists():
        return None
    return read_json(path)


def window_for_task(round_data: Optional[Dict[str, Any]], task_id: str, owner: str) -> str:
    if isinstance(round_data, dict):
        tasks = round_data.get("tasks", {})
        if isinstance(tasks, dict):
            for window, values in tasks.items():
                ids = values if isinstance(values, list) else [values]
                if task_id in {str(item) for item in ids if item}:
                    return str(window)
    return owner


def close_path_label(policy: Dict[str, Any]) -> str:
    if policy.get("conflict"):
        return "POLICY_CONFLICT — do not dispatch or close"
    if policy.get("independent_verification"):
        return "独立验收 worker_done → verifying → verified → integrated → done"
    return "短路径 worker_done → integrated → done（actor M1）"


def format_requirements(manifest: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for requirement in manifest.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        req_id = str(requirement.get("id") or "?")
        text = str(requirement.get("text") or "").strip() or "(no text)"
        command = ""
        if isinstance(requirement.get("verify_cmd"), str) and requirement["verify_cmd"].strip():
            command = requirement["verify_cmd"].strip()
        elif isinstance(requirement.get("verify"), str) and requirement["verify"].strip():
            command = requirement["verify"].strip()
        lines.append(f"- {req_id}: {text}")
        if command:
            lines.append(f"  verify: {command}")
    return lines or ["- (none)"]


def role_hard_rules(role: str, policy: Dict[str, Any]) -> List[str]:
    shared = [
        "- 只改 allowed_paths 内的文件；禁止打开网页预览",
        "- 验收命令只检查已有文件，不负责生成业务文件",
        "- 状态只用 taskctl transition，不要整份覆盖 manifest.json",
        "- 不要自行标 done；M1 唯一收口",
        f"- 收口路径：{close_path_label(policy)}",
    ]
    if role == "scout":
        return [
            "- 当前角色：斥候。只读调查，列路径与可疑点，建议最小范围",
            "- 禁止改代码、禁止声称已修好、禁止改 Registry 为 done",
        ] + shared
    if role == "verifier":
        return [
            "- 当前角色：搜剿 / 独立验收。只读实现，禁止修改 src 与工人文件",
            "- reviewer 窗口不得等于工人窗；写 verify-report.json",
            "- 工人没跑终端或缺证据则 fail，不要凭感觉放行",
        ] + shared
    return [
        "- 当前角色：主力。最小改动实现；本窗跑验收命令并贴终端原文",
        "- 结束时写 worker-report.json：covered_requirements、evidence.path、changed_files、tests",
        "- changed_files 不要报 manifest.json / rerun.json / verify-report.json",
        "- 工人跑完不会自动交给 M1；须用户传「{窗号} 已完成，请查收」",
    ] + shared


def role_report_format(role: str) -> List[str]:
    if role == "scout":
        return [
            "- 只交斥候报告：路径、可疑点、建议最小范围。不要写 worker-report.json 当完工",
        ]
    if role == "verifier":
        return [
            "- 写 .task/TASK-xxx/verify-report.json：reviewer、result、checked_requirements、missing",
            "- 然后：transition TASK-xxx verifying --actor verifier；verified --actor verifier",
        ]
    return [
        "- 写 .task/TASK-xxx/worker-report.json，然后：transition TASK-xxx worker_done --actor worker",
    ]


def cmd_brief(args: argparse.Namespace, root: Path) -> int:
    role = str(args.role or "").strip().lower()
    if role not in BRIEF_ROLES:
        print("BRIEF_FAIL: illegal role; use worker|scout|verifier")
        return 1
    try:
        require_task_id(args.task_id)
    except ValueError as exc:
        print(f"BRIEF_FAIL: {exc}")
        return 1
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print(f"BRIEF_FAIL: missing task {args.task_id}")
        for error in errors:
            print(f"- {error}")
        return 1
    try:
        round_data = load_round(root)
    except ValueError as exc:
        print(f"BRIEF_FAIL: {exc}")
        return 1
    policy = verification_policy(manifest)
    owner = str(manifest.get("owner") or "")
    window = window_for_task(round_data, args.task_id, owner)
    allowed = manifest.get("allowed_paths")
    allowed_lines = (
        [f"- {item}" for item in allowed]
        if isinstance(allowed, list) and allowed
        else ["- (empty — fill before dispatch)"]
    )
    print(f"BRIEF {args.task_id} role={role}")
    print(f"# 任务简报 — {args.task_id} / {role}")
    print()
    print(f"- 窗号: {window or '(unassigned)'}")
    print(f"- 标题: {manifest.get('title') or '(none)'}")
    print(f"- owner: {owner or '(none)'}")
    print(f"- 任务状态: {manifest.get('status', 'pending')}")
    print(f"- risk: {policy['risk']}")
    print(f"- attempt: {policy['attempt']}")
    print(f"- 收口路径: {close_path_label(policy)}")
    if policy.get("conflict"):
        print(f"- POLICY_CONFLICT: {policy['conflict']}")
    print()
    print("## allowed_paths")
    print("\n".join(allowed_lines))
    print()
    print("## source_refs（来源 → R）")
    refs = manifest.get("source_refs")
    if not isinstance(refs, list) or not refs:
        print("- (missing — 用户需求必须先写入 source_refs 再派工)")
    else:
        for item in refs:
            if not isinstance(item, dict):
                continue
            mapped = item.get("maps_to")
            mapped_text = ", ".join(str(value) for value in mapped) if isinstance(mapped, list) else "(none)"
            print(f"- {item.get('id')}: {item.get('text') or ''} → {mapped_text}")
    print()
    print("## R 项与验收")
    print("\n".join(format_requirements(manifest)))
    print()
    print("## 硬规则")
    print("\n".join(role_hard_rules(role, policy)))
    print()
    print("## 报告格式")
    print("\n".join(role_report_format(role)))
    print()
    print("## 开工句")
    print(f"我是 {window or '{窗号}'} 窗口。只按本简报执行。不要打开网页预览。")
    return 0


def summarize_hook_runs(root: Path) -> List[str]:
    path = hook_log_path(root)
    if not path.exists():
        return ["- 无 hook-runs.jsonl（不要把缺日志当成完成证据）"]
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return ["- hook-runs.jsonl 为空"]
    recent = rows[-6:]
    lines = [f"- 共 {len(rows)} 行（最多 100）；最近 {len(recent)} 行："]
    for raw in recent:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            lines.append(f"  - (invalid json) {raw[:80]}")
            continue
        lines.append(
            "  - "
            f"phase={entry.get('phase')} host={entry.get('host')} "
            f"source={entry.get('source')} result={entry.get('audit_result')} "
            f"run_id={entry.get('run_id')}"
        )
    return lines


def list_blocker_files(root: Path) -> List[str]:
    directory = root / "docs" / "BLOCKERS"
    if not directory.is_dir():
        return []
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    )


def next_step_for_task(manifest: Dict[str, Any], policy: Dict[str, Any]) -> str:
    status = str(manifest.get("status", "pending"))
    if policy.get("conflict"):
        return "停止收口，修复 POLICY_CONFLICT"
    if status == "pending":
        return "transition in_progress --actor M1，再派工人"
    if status == "in_progress":
        return "工人改文件、本窗跑验收、transition worker_done --actor worker"
    if status == "worker_done":
        if policy.get("independent_verification"):
            return "派独立验收窗 verifying → verified，禁止 worker_done → integrated"
        return "M1 本窗重跑 Full Gate 后 integrated → done"
    if status == "verifying":
        return "verifier 写 verify-report 后 transition verified --actor verifier"
    if status == "verified":
        return "M1 本窗重跑后 integrated → done"
    if status == "integrated":
        return "transition done --actor M1"
    if status == "done":
        return "已闭环；不要再改同一任务除非 reopen"
    if status in {"paused", "blocked", "reopened"}:
        return f"当前 {status}：由 M1 决定是否 in_progress 或保持阻塞"
    return "对照策略表选择唯一合法下一跳"


def cmd_handoff(root: Path) -> int:
    if not task_root(root).is_dir():
        print("HANDOFF_FAIL: no .task")
        return 1
    try:
        round_data = load_round(root)
    except ValueError as exc:
        print(f"HANDOFF_FAIL: {exc}")
        return 1
    print("HANDOFF")
    print("# M1 接班简报")
    print()
    print("只读生成物。不要手写转述替代本简报。查收仍须本窗重跑；M1 唯一收口。")
    print()
    if round_data is None:
        print("## 本轮")
        print("- 无 round.json")
    else:
        print("## 本轮")
        print(f"- round_id: {round_data.get('round_id') or '?'}")
        windows = round_data.get("expected_windows") or []
        print(f"- 本轮窗口: {', '.join(str(item) for item in windows) or '(none)'}")
        receipts = round_data.get("receipts") or []
        print(f"- receipts: {', '.join(str(item) for item in receipts) or '(none)'}")
        print(f"- check_requested: {round_data.get('check_requested', False)}")
        print(f"- gears: {format_gears_line(round_data)}")
        window_status = round_data.get("window_status")
        if isinstance(window_status, dict):
            for window in windows:
                print(f"- WINDOW {window}: {window_status.get(window, 'MISSING')}")
    print()
    print("## 迁移")
    lock_file = lock_path(root)
    if not lock_file.exists():
        print("- 无 skill-lock.json。旧项目先 migrate-project，再 --check，未 MIGRATE_READY 不要开发新功能")
    else:
        try:
            lock = read_json(lock_file)
        except ValueError as exc:
            print(f"- invalid skill-lock.json ({exc})")
            lock = {}
        print(f"- skill_version: {lock.get('skill_version') or '?'}")
        print(f"- taskctl_version: {lock.get('taskctl_version') or '?'}")
        print(f"- migrated_at: {lock.get('migrated_at') or '?'}")
        print(f"- backup: {lock.get('backup') or '(none)'}")
        print(f"- status: {lock.get('status') or '?'}")
        checks = lock.get("checks") or {}
        if checks:
            print(
                "- checks: "
                + ", ".join(f"{key}={checks.get(key)}" for key in ("basic", "full", "hook", "negative"))
            )
        else:
            print("- checks: 尚未运行 migrate-project --check")
        if lock.get("status") != "ready":
            print("MIGRATE_CHECK_INCOMPLETE")
    print()
    print("## 任务")
    statuses = load_task_statuses(root)
    if not statuses:
        print("- 无 TASK-*")
    open_items: List[str] = []
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if not manifest_file.exists():
            print(f"- {directory.name}: missing manifest.json")
            open_items.append(f"{directory.name} missing manifest")
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError as exc:
            print(f"- {directory.name}: invalid manifest ({exc})")
            open_items.append(f"{directory.name} invalid manifest")
            continue
        task_id = str(manifest.get("task_id", directory.name))
        policy = verification_policy(manifest)
        step = next_step_for_task(manifest, policy)
        print(
            f"- {task_id}: status={manifest.get('status', 'pending')} "
            f"owner={manifest.get('owner', '-')} risk={policy['risk']} "
            f"attempt={policy['attempt']} path={close_path_label(policy)}"
        )
        print(f"  下一步: {step}")
        if str(manifest.get("status", "pending")) != "done" or policy.get("conflict"):
            open_items.append(f"{task_id}: {step}")
    print()
    print("## 未闭环")
    if open_items:
        for item in open_items:
            print(f"- {item}")
    else:
        print("- 无（全部 done 且无 POLICY_CONFLICT）")
    blockers = list_blocker_files(root)
    print()
    print("## BLOCKERS")
    if blockers:
        for path in blockers:
            print(f"- {path}")
    else:
        print("- 无 docs/BLOCKERS 文件")
    print()
    print("## Hook")
    print("\n".join(summarize_hook_runs(root)))
    print()
    print("## 建议")
    if open_items:
        print("- 先处理未闭环任务；生成 brief 再贴给工人/验收人，不要手写转述")
        print("- 出现 POLICY_CONFLICT / GEAR_VIOLATION / HOOK_EVIDENCE_MISSING / PARALLEL_FAIL 则停止收口")
    else:
        print("- 本轮任务已闭环。新需求先 init + brief，不要只改聊天话术")
    print("- 窗口状态 ≠ 任务完成；不要把 M/C verified 当成 TASK done")
    print("- 旧项目未 MIGRATE_READY 则不要开发新功能")
    return 0


def cmd_selftest() -> int:
    """Run test_*.py beside this file so every host copy stays self-contained."""
    here = Path(__file__).resolve().parent
    tests = sorted(
        path for path in here.glob("test_*.py") if path.is_file()
    )
    if not tests:
        print("SELFTEST FAIL: no test_*.py next to taskctl.py")
        return 1
    failed = 0
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for test in tests:
        print(f"SELFTEST RUN {test.name}")
        completed = subprocess.run(
            [sys.executable, str(test)],
            cwd=str(here),
            env=env,
        )
        if completed.returncode != 0:
            failed += 1
            print(f"SELFTEST FAIL {test.name}")
        else:
            print(f"SELFTEST PASS {test.name}")
    if failed:
        print(f"SELFTEST FAIL count={failed}")
        return 1
    print("SELFTEST PASS")
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
        description="multi-window_M v0.31 task gate",
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
    migrate = sub.add_parser("migrate-project", help="backup, report, copy this v0.31 taskctl, then --check")
    migrate.add_argument("--destination", default="scripts/taskctl.py")
    migrate.add_argument("--force", action="store_true", help="replace an existing destination after backup")
    migrate.add_argument(
        "--check",
        action="store_true",
        help="run basic/full/hook/negative smokes; require MIGRATE_READY before new work",
    )
    status_parser = sub.add_parser("status", help="show window and task states")
    status_parser.add_argument(
        "--markdown",
        action="store_true",
        help="render Registry status table and RECEIPT summary from .task/",
    )
    status_parser.add_argument(
        "--write",
        action="store_true",
        help="write docs/TASK-STATUS.md (implies --markdown); do not hand-edit that file",
    )

    transition = sub.add_parser("transition", help="change a task status through the controlled state machine")
    transition.add_argument("task_id")
    transition.add_argument("status", choices=sorted(TASK_STATES))
    transition.add_argument("--actor", choices=["M1", "worker", "verifier", "manual"], required=True)
    transition.add_argument("--reason")

    reopen = sub.add_parser("reopen", help="reopen a task and increment attempt")
    reopen.add_argument("task_id")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--actor", choices=["M1"], default="M1")
    brief = sub.add_parser("brief", help="print a copy-ready task brief for a role")
    brief.add_argument("task_id")
    brief.add_argument("--role", required=True, help="worker, scout, or verifier")
    sub.add_parser("handoff", help="print an M1 succession brief from .task/")
    sub.add_parser("selftest", help="run bundled tests next to this script")
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
            return cmd_status(
                root,
                markdown=bool(getattr(args, "markdown", False)),
                write=bool(getattr(args, "write", False)),
            )
        if args.command == "transition":
            return cmd_transition(args, root)
        if args.command == "reopen":
            return cmd_reopen(args, root)
        if args.command == "brief":
            return cmd_brief(args, root)
        if args.command == "handoff":
            return cmd_handoff(root)
        if args.command == "selftest":
            return cmd_selftest()
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
