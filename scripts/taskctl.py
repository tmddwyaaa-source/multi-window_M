#!/usr/bin/env python3
"""Minimal, dependency-free task gate for the multi-window-m-036 skill (v0.36).

Core behavior is host-agnostic. migrate-project backs up, writes a report and
skill-lock, then copies. Feature work waits for migrate-project --check
(MIGRATE_READY). Hook still does not mutate status.

v0.36 adds the cross-host contract that Codex and DSH agreed on:
  * `.task/manifest.json` and `.task/round.json` carry `schema_version`; a file
    newer than this host reads -> UNSUPPORTED_SCHEMA and fail closed (view only).
  * `status` is a read-only view: it never executes shell commands. Run
    `gate <task-id>` / `audit-round` for a real check, or `status --deep` to
    explicitly re-run the Full Gate per task.
  * `round-close` archives round.json to .task/rounds/ and only when every task
    is done and the audit passes.
  * UNREPLAYABLE_COMMAND prechecks `tests[].command` shapes before closure. It
    is an evidence error: it never advances the block counter.
  * STALE_REPORT warns (never fails) when a report's own changed_files were
    modified after it was written, compared by content hash - never by mtime.

Host evidence lives in two ledgers beside the round: `.task/hook-runs.jsonl`
(cursor / codex / zcode stop hooks) and `.task/dsh-runs.jsonl` (DeepSeek
Harness `Stop` / `SubagentStop` through the dsh hooks bridge). Both are read by
audit-round; neither writes status.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
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
    "round-close",
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
    "attempt",
    "reassign",
    "selftest",
    "brief",
    "packet",
    "handoff",
    "adjudicate",
    "assign-verifier",
    "sync-worker-route",
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
DSH_LEDGER_MAX_LINES = 400
RERUN_TIMEOUT_SEC = 60
SKILL_VERSION = "0.36"
DSH_HOST = "dsh"
# `.task/` 共同文件的**契约版本**，与 SKILL_VERSION（技能行为版本）分开：
# 技能可以升版而 schema 不变。宿主**读写**用的是自己的版本常量
# （DSH_READS_SCHEMA / DSH_WRITES_SCHEMA）。读到更高的 schema 必须 fail closed：
# 只允许查看，禁止变更状态，禁止收口。这样两个宿主永远不会用"自己旧的理解"
# 去静默改写"对方新写的数据"。
SCHEMA_VERSION = 1
SCHEMA_FIELD = "schema_version"
DSH_READS_SCHEMA = SCHEMA_VERSION
DSH_WRITES_SCHEMA = SCHEMA_VERSION
UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
# 反方向：项目已声明更高的 schema 契约，却有文件缺版本标记 → 被**更旧的宿主**写过。
# 旧宿主不认识 `schema_version`，既不维护它也不报错，只能由当前宿主发现。
SCHEMA_REGRESSION_RISK = "SCHEMA_REGRESSION_RISK"
SCHEMA_KEY = "_schema"
HARD_STOP = "HARD_STOP"
REASSIGN_REQUIRED = "REASSIGN_REQUIRED"
UNREPLAYABLE_COMMAND = "UNREPLAYABLE_COMMAND"
# 同一次失败/交接被重复提交时不再重复计数、不重复写历史（方向文件 §6.1：
# 「一次真实失败计一次；重复处理同一次失败/交接不重复增加计数或历史」）。
DUPLICATE_EVENT = "DUPLICATE_EVENT"
# 同一修复轮次/卡点/负责人的再次提交 = 同一次失败的补充说明（不增加计数）。
FAILURE_SUPPLEMENTED = "FAILURE_SUPPLEMENTED"
# 结构错误：必需命令字段缺失或拼错，导致证据无法解释（方向文件 §3「结构错误」）。
# 要点：指出文件、条目、字段和正确写法，**不能静默跳过**；合法可选元数据不拒。
UNKNOWN_FIELD = "UNKNOWN_FIELD"
# `tests[]` / `evidence[]` 的字段白名单。值是"近义写法"，只用于提示，不做静默容错：
# 必须让写错的人看到正确字段名，而不是让证据被悄悄丢掉。
TEST_ITEM_FIELDS = {
    "command": ("cmd", "verify_cmd", "run"),
    "note": ("notes", "why", "comment"),
    "requirement_id": ("requirement", "req", "r"),
    "exit_code": ("exit", "code", "status"),
    "expected": ("expect",),
}
EVIDENCE_ITEM_FIELDS = {
    "path": ("file", "evidence", "artifact"),
    "file": ("path",),
    "requirement_id": ("requirement", "req", "r"),
    "note": ("notes", "why", "comment"),
}
# 命令里出现的像文件名的 token；只用来做"已声明却缺失"的定位，不做存在性判定。
COMMAND_FILE_RE = re.compile(
    r"[A-Za-z0-9_./@\\-]*[A-Za-z0-9_@-]\.(?:js|mjs|cjs|ts|py|json|md|txt|html|css|pas)\b"
)
# 会改写 `.task/` 共同文件的子命令：schema 比本宿主新时一律拒绝。
MUTATING_COMMANDS = {
    "init",
    "round-init",
    "round-close",
    "receipt",
    "request-check",
    "assign-verifier",
    "sync-worker-route",
    "transition",
    "reopen",
    "attempt",
    "reassign",
    "hook-audit",
    "migrate-project",
    "adjudicate",
}
LOCK_REL = ".task/skill-lock.json"
MIGRATE_REPORT_REL = "docs/MIGRATE-REPORT.md"
SOURCE_HOST = {
    "cursor-stop": "cursor",
    "codex-stop": "codex",
    "zcode-stop": "zcode",
    "dsh-stop": DSH_HOST,
    "dsh-subagent-end": DSH_HOST,
    "dsh-session-start": DSH_HOST,
    "dsh-prompt": DSH_HOST,
    "dsh-pre-tool": DSH_HOST,
    "dsh-post-tool": DSH_HOST,
    "manual": "manual",
}
# A DSH hook is a one-shot observation: it writes exactly one phase entry, and
# a complete pair comes from two invocations sharing one run_id (the host
# session id). Only these two mean "the window had its turn ended", which is
# what a stop-hook supervision claim needs.
STOP_HOOK_SOURCES = {"cursor-stop", "codex-stop", "zcode-stop", "dsh-stop", "dsh-subagent-end"}
DSH_SOURCES = {source for source in SOURCE_HOST if source.startswith("dsh-")}
# `SubagentStop` is the strongest dsh signal: it carries the real agent id, so
# a supervised round can require that a subagent actually ended, not merely
# that some turn stopped.
DSH_STRONG_SOURCES = {"dsh-subagent-end"}
# 同一卡点（同一根因）的三级阶梯上限。这不是"节奏偏好"，是跨宿主稳定性规则：
# 防止同一错误理解被无限重复。第 1 次 fail 复用并复述；第 2 次 fail 换真实负责人；
# 第 3 次 fail 硬停写 BLOCKERS。改根因必须给证据，否则视为靠改名重置计数。
MAX_BLOCK_ATTEMPTS = 3
# 同一卡点第 N 次 fail 时，必须换真正不同的负责人（第 2 级阶梯）。
REASSIGN_ON_SAME_BLOCK_FAILURE = 2
BLOCK_REPLACEMENT_HINT = (
    "第 2 次 fail：换真正不同的负责窗口或独立执行上下文，并更新 owner；"
    "宿主给不出合格新执行者时不得伪造换人，写 BLOCKERS NO_ELIGIBLE_REPLACEMENT_WORKER"
)
NO_ELIGIBLE_REPLACEMENT = "NO_ELIGIBLE_REPLACEMENT_WORKER"
# 一任务两职责：`round.tasks` 只表达 worker 实现路由；独立验收路由写在
# `round.verifier_assignments`。两者必须可区分、可审计、不可互相冒充。
VERIFIER_ASSIGNMENT_MISSING = "VERIFIER_ASSIGNMENT_MISSING"
VERIFIER_ASSIGNMENT_CONFLICT = "VERIFIER_ASSIGNMENT_CONFLICT"
WORKER_ASSIGNMENT_CONFLICT = "WORKER_ASSIGNMENT_CONFLICT"
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


def normalized_path(value: Any) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


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


def schema_version_of(data: Any) -> int:
    """读取共同文件声明的 schema 版本；缺失视为 0（0.34 及更早的旧文件）。"""
    if not isinstance(data, dict):
        return 0
    raw = data.get(SCHEMA_FIELD)
    if isinstance(raw, bool) or raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return -1


def schema_mismatch(data: Any) -> bool:
    """只有"文件比本宿主新"才阻断；文件更旧走迁移兼容路径。"""
    return schema_version_of(data) > DSH_READS_SCHEMA


def unsupported_schema_error(rel: str, data: Any) -> str:
    return (
        f"{UNSUPPORTED_SCHEMA}: {rel} declares schema_version="
        f"{schema_version_of(data)}, but this host reads at most {DSH_READS_SCHEMA}. "
        "只允许查看：禁止变更状态、禁止收口。请升级本技能版本后再继续。"
    )


def shared_schema_files(root: Path) -> List[Path]:
    """共同文件清单：round.json 与每个任务的 manifest.json。"""
    files: List[Path] = []
    round_file = task_root(root) / "round.json"
    if round_file.is_file():
        files.append(round_file)
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if manifest_file.is_file():
            files.append(manifest_file)
    return files


def schema_problems(root: Path) -> List[str]:
    """read-only 检查：列出所有"比本宿主新"的共同文件。"""
    problems: List[str] = []
    for path in shared_schema_files(root):
        try:
            data = read_json(path)
        except ValueError:
            continue
        if schema_mismatch(data):
            problems.append(
                unsupported_schema_error(
                    normalized_path(str(path.relative_to(root))), data
                )
            )
    return problems


def guard_mutation(root: Path) -> int:
    """fail closed：schema 比本宿主新时，任何写操作都必须拒绝。

    返回 0 表示可以继续；返回 1 表示已打印原因并必须中止。
    """
    problems = schema_problems(root)
    if not problems:
        return 0
    print("RESULT FAIL")
    for problem in problems:
        print(f"- {problem}")
    return 1


def is_mutating_command(tokens: List[str]) -> bool:
    """写共同文件的子命令。只读子命令（status/gate/audit/brief/packet/handoff/selftest）除外。"""
    subcommand = next((token for token in tokens if token in KNOWN_COMMANDS), "")
    return subcommand in MUTATING_COMMANDS


def project_contract_schema(root: Path) -> int:
    """项目**自己声明**的 schema 契约版本（来自 `.task/skill-lock.json`）。

    0 表示这个项目从未被任何带版本意识的宿主写过——也就是真正的历史项目。
    只要它一直是 0，缺 `schema_version` 的文件就照旧兼容。一旦某个文件被写成
    1（`init` / `round-init` / `migrate-project` 都会升级锁），契约就变成 1，
    此后"缺版本标记"不再可能是历史遗留，只可能是**被旧宿主操作过**。
    """
    path = lock_path(root)
    if not path.is_file():
        return 0
    try:
        lock = read_json(path)
    except ValueError:
        return 0
    raw = lock.get(SCHEMA_FIELD)
    if isinstance(raw, bool) or raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def disk_schema_high_water(root: Path) -> int:
    """磁盘上共同文件声明过的**最高** schema 版本。

    项目契约应当与之一致：若锁低于它，说明这个项目确实有新版写出的数据，
    锁该被拉高（而不是把旧文件判成回归）。
    """
    highest = 0
    for path in shared_schema_files(root):
        try:
            highest = max(highest, schema_version_of(read_json(path)))
        except ValueError:
            continue
    return highest


def schema_regression_problems(root: Path) -> List[str]:
    """活动轮 / **已开工**任务缺版本标记，而项目已声明更高契约 → 说明被旧宿主写过。

    这是 `UNSUPPORTED_SCHEMA` 的**反方向**：那个防"未来版写的数据被现在这版乱改"，
    这个防"现在这版写的数据被更旧的宿主乱改"。

    **判据收窄**（否则会误伤）：
      - 先把项目契约与磁盘上的最高版本对齐——项目里只要有任何带标记的文件，
        锁就不该更低；
      - `round.json` 只在**本轮已把任务派出去**（`round.tasks` 非空）时才算；
      - 任务只在**已开工**（`status` 不是 pending/paused）时才算。
    未开工的模板本来就可以没有标记，不该被当成回归。
    """
    declared = max(project_contract_schema(root), disk_schema_high_water(root))
    if declared <= 0:
        return []
    # 项目里若**没有任何**带标记的文件，说明契约是刚被工具写下的（例如 `init`），
    # 此时把旧格式文件判成"回归"是误报。只有在确实存在带标记文件时才启用回归检测。
    if disk_schema_high_water(root) <= 0:
        return []
    problems: List[str] = []
    round_file = round_path(root)
    if round_file.is_file():
        try:
            data = read_json(round_file)
        except ValueError:
            data = None
        if (
            isinstance(data, dict)
            and schema_version_of(data) < declared
            and current_round_task_ids(data)
        ):
            problems.append(
                f"{SCHEMA_REGRESSION_RISK}: .task/round.json declares "
                f"schema_version={schema_version_of(data)} but the project declares "
                f"{declared}：这一轮是被**更旧的宿主**写出/改写的，"
                "按旧规则收口会与现行规则冲突。请用当前版本的 taskctl 重新 round-init，"
                "或先确认没有旧宿主在操作这个项目。"
            )
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if not manifest_file.is_file():
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError:
            continue
        if schema_version_of(manifest) >= declared:
            continue
        status = str(manifest.get("status") or "pending")
        if status in {"pending", "paused"}:
            continue  # 未开工的模板允许没有标记
        problems.append(
            f"{SCHEMA_REGRESSION_RISK}: .task/{directory.name}/manifest.json declares "
            f"schema_version={schema_version_of(manifest)} but the project declares "
            f"{declared}：该任务已开工却被更旧的宿主改写，"
            "`block_attempts` 等新字段可能被绕过。"
        )
    return problems


def ensure_contract_schema(root: Path) -> None:
    """写入带版本的文件时，同步把项目的 schema 契约升到当前值。

    调用点只在**真正写了新文件之后**（`init` / `round-init` / `migrate-project`）。
    绝不在读取路径上调用：否则第一次读取就会把契约"自动补上"，回归检测随之失效。
    """
    path = lock_path(root)
    lock: Dict[str, Any] = {}
    if path.is_file():
        try:
            lock = read_json(path)
        except ValueError:
            lock = {}
    if schema_version_of(lock) >= DSH_WRITES_SCHEMA:
        return
    lock[SCHEMA_FIELD] = DSH_WRITES_SCHEMA
    lock.setdefault("skill_version", SKILL_VERSION)
    lock["schema_declared_at"] = now_iso()
    write_json(path, lock)


def guard_schema_regression(root: Path) -> int:
    """回归检测的 fail closed 出口：返回非 0 表示必须中止写操作。"""
    problems = schema_regression_problems(root)
    if not problems:
        return 0
    print("RESULT FAIL")
    for problem in problems:
        print(f"- {problem}")
    return 1


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


def repair_cycle(manifest: Dict[str, Any]) -> int:
    """当前**修复轮次**：每一轮"施工 → 失败"就是一个修复轮。

    它替代"自由文字"作为失败事件的身份（Codex 审阅问题 1）：
    修复一轮之后**再次出现同样错误**是**新的失败**，必须计数；
    而同一次失败被重复提交（同一轮内）不应计两次。
    """
    return max(0, int(manifest.get("repair_cycle", 0) or 0))


def advance_repair_cycle(manifest: Dict[str, Any]) -> int:
    """推进**修复轮次**。只在真实的施工/重新交付事件上调用：

    - `transition` 进入 `in_progress`（重新开始施工）；
    - `transition` 进入 `worker_done`（工人重新交付）；
    - 硬停被显式裁决解除并恢复施工。

    查看、出简报、重复传话、同负责人确认交接都**不**推进轮次。
    """
    manifest["repair_cycle"] = repair_cycle(manifest) + 1
    return manifest["repair_cycle"]


def work_event_advances_cycle(target: str) -> bool:
    """哪些状态代表"期间发生过施工/交付"——这些转换才推进轮次。"""
    return target in {"in_progress", "worker_done"}


def hard_stop_active(manifest: Dict[str, Any]) -> bool:
    """当前卡点是否处于**硬停**（第三次同根因失败）。

    硬停不是"换个 owner 就能继续"的普通阻塞：它是"这对根因不能再盲改"的裁决结果。
    """
    return bool(manifest.get("hard_stop"))


def hard_stop_blocks(status: str) -> bool:
    """硬停期间**禁止**进入的状态：恢复施工与任何完成态。"""
    return status in {
        "in_progress",
        "reopened",
        "worker_done",
        "verifying",
        "verified",
        "integrated",
        "done",
    }


def hard_stop_refusal(task_id: str, target: str) -> str:
    return (
        f"HARD_STOP_ACTIVE: {task_id} 当前卡点已硬停（第三次同根因失败），"
        f"不能通过普通换人/转换恢复施工（本次目标状态={target}）。"
        "硬停只能由**明确的裁决**解除：用 `--adjudicate-hard-stop --reason <证据>`，"
        "或换根因（`--new-root` 并给证据）。只读命令与写阻塞记录不受影响。"
    )


def current_attempts(manifest: Dict[str, Any]) -> int:
    """当前卡点的失败次数（不含首次派工）。

    与 `attempt` 的分工：`attempt` 是任务级历史（只驱动验收策略），
    `block_attempts` 是**卡点级**计数（只驱动三级阶梯）。有 `block_id` 时
    两者互不重叠。

    例外（与 Codex 共同确认，保留）：**0.34 及更早的 manifest 没有
    `block_attempts`**，此时回落到任务级 `attempt`，并视为
    `LEGACY_UNSCOPED`。这是保守兼容：旧项目不会因为缺字段而**凭空多出**
    重试次数。迁移时会在迁移报告里写明这一点。
    """
    block = manifest.get("block_attempts")
    if isinstance(block, dict) and block.get("block_id"):
        return max(0, int(block.get("attempts", 0) or 0))
    return max(0, int(manifest.get("attempt", 0) or 0))


def is_legacy_unscoped(manifest: Dict[str, Any]) -> bool:
    """`block_attempts` 缺失时，卡点计数回落到任务级 attempt 的旧数据形态。"""
    block = manifest.get("block_attempts")
    return not (isinstance(block, dict) and block.get("block_id"))


def last_failure_entry(block: Any) -> Optional[Dict[str, Any]]:
    for item in reversed(block_history_entries(block)):
        if isinstance(item, dict) and item.get("failure_id"):
            return item
    return None


def effective_failure_cycle(manifest: Dict[str, Any], block: Any) -> int:
    """这次失败属于第几轮修复 —— 读**显式、单调**的轮次记录。

    Codex rev3 Q3' 要求：不能用"最终状态值相同/不同"当主判据——那只反映当前值，
    不反映期间发生过什么；而且会**忽略已经记录的新轮次**。
    所以轮次由**真实的施工/重新交付事件**推进（见 `advance_repair_cycle` 的调用点：
    进入 `in_progress`、重新交付 `worker_done`、硬停裁决解除），
    这里只做单调回退保护：不得低于最近一次失败记录的轮次。
    """
    current = repair_cycle(manifest)
    last = last_failure_entry(block)
    if last is None:
        return current
    return max(current, max(0, int(last.get("repair_cycle", 0) or 0)))
def failure_event_id(block_id: str, owner: str, cycle: int) -> str:
    """**一次失败事件**的身份 = 修复轮次 + 卡点 + 负责人。

    **`reason` 不参与身份**（Codex rev2 Q3）：它是解释材料，不是事件身份。
    - 同一修复轮次 + 同卡点 + 同负责人 = **同一次失败**：后续提交只作"补充说明"，
      记录证据但**不增加计数**（所以"换一句话再说一遍"不会重复计数）；
    - 修复一轮之后再出现同样错误 = **新的失败**（轮次变了），照常计数；
    - 换根因请用不同的 `block_id`（既有规则），不要靠换措辞表示新失败。
    """
    payload = "\n".join([str(cycle), str(block_id or ""), str(owner or "")])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def failure_supplement_error(event_id: str) -> str:
    return (
        f"{FAILURE_SUPPLEMENTED}: 同一修复轮次、同一卡点、同一负责人的失败已记录过"
        f"（事件 {event_id}）——本次仅作**补充说明**，不增加失败次数。"
        "换根因请用不同的 --block-id；确实换了执行者请用 reassign。"
    )


def handoff_fingerprint(block_id: str, from_owner: str, to_owner: str, reason: str) -> str:
    """同一次交接的指纹：同 block + 同 from→to + 同原因 = 同一次交接。"""
    payload = "\n".join(
        [str(block_id or ""), str(from_owner or ""), str(to_owner or ""), str(reason or "").strip()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def block_history_entries(block: Any) -> List[Any]:
    if isinstance(block, dict) and isinstance(block.get("history"), list):
        return block["history"]
    return []


def duplicate_failure_error(block: Any, fingerprint: str) -> str:
    return (
        f"{DUPLICATE_EVENT}: 这次失败与已记录的失败指纹相同（{fingerprint}）——"
        "同一个卡点 + 同一个负责人 + 同一个原因只计一次。"
        "换根因请用 --new-root 并给证据；换负责人请用 reassign。"
    )


def bump_block_attempt(
    manifest: Dict[str, Any],
    *,
    block_id: str,
    reason: str,
    owner: str,
    new_root: bool = False,
) -> Tuple[Dict[str, Any], str, bool]:
    """记录**一次新的**卡点失败；同一 block_id 累加，改根因必须给证据。

    去重按**失败事件** = 修复轮次 + 卡点 + 负责人（**不含 reason**，Codex rev2 Q3）：
    - 同一次失败再次提交 → 记补充说明、**不增加计数**，`supplemented=True`；
    - 修复一轮之后再出现同样错误 → 新事件，照常计数；
    - 换人不增加失败次数（交接由 `assignment_history` 承载）。

    Returns (block_record, error, supplemented)。
    `error` 非空且 `supplemented=False` 表示**拒绝本次计数**。
    """
    block_id = str(block_id or "").strip()
    if not block_id:
        return {}, "block_id must be a non-empty id for the current root cause", False
    reason = str(reason or "").strip()
    block = manifest.get("block_attempts")
    known = block.get("block_id") if isinstance(block, dict) else None
    if known and known != block_id and not new_root:
        return {}, (
            f"BLOCK_ID_CHANGE_REQUIRES_EVIDENCE: block_id={block_id} differs from the current "
            f"block_id={known}; renaming a root cause does not reset the counter. "
            "Pass --new-root with acceptance evidence to declare a genuinely new root cause."
        ), False
    if new_root:
        if not reason:
            return {}, "--new-root requires an evidence-bearing --reason", False
        block = {"block_id": block_id, "attempts": 0, "history": []}
        history: List[Any] = []
        # 硬停属于**那一个根因**。换根因（带证据）意味着旧根因已处理，
        # 硬停随之解除并留痕——否则新根因会被旧硬停永久挡住。
        if manifest.get("hard_stop"):
            manifest["hard_stop"] = False
            manifest.setdefault("hard_stop_cleared", []).append(
                {"at": now_iso(), "by": "new-root", "reason": reason, "block_id": block_id}
            )
    else:
        if not isinstance(block, dict) or not block.get("block_id"):
            block = {"block_id": block_id, "attempts": 0, "history": []}
        history = block_history_entries(block)
    cycle = effective_failure_cycle(manifest, block)
    event_id = failure_event_id(block_id, owner, cycle)
    existing = next(
        (
            item
            for item in history
            if isinstance(item, dict) and item.get("failure_id") == event_id
        ),
        None,
    )
    if existing is not None:
        # 同一次失败：**记录补充说明，但不增加计数**（reason 不是事件身份）。
        supplements = existing.setdefault("supplements", [])
        if not isinstance(supplements, list):
            supplements = []
            existing["supplements"] = supplements
        if reason:
            supplements.append({"at": now_iso(), "reason": reason})
        block.update({"block_id": block_id, "attempts": block.get("attempts", 0), "history": history})
        manifest["block_history"] = history
        return block, failure_supplement_error(event_id), True
    attempts = max(0, int(block.get("attempts", 0) or 0)) + 1
    if attempts > MAX_BLOCK_ATTEMPTS:
        return {}, (
            f"卡点上限 {MAX_BLOCK_ATTEMPTS} 次已到（block_id={block_id}）："
            "禁止继续对同一根因盲改；写 docs/BLOCKERS/ 升级报告并点名下一步。"
        ), False
    history.append(
        {
            "attempt": attempts,
            "block_id": block_id,
            "failure_id": event_id,
            "repair_cycle": cycle,
            "at": now_iso(),
            "owner": owner or manifest.get("owner") or "?",
            "reason": reason,
            # 记录"这次失败**之前**的状态"；失败处理结束后由调用方补 `status_after`。
            # 下一条失败靠"当前状态 vs status_after"判断期间有没有发生新的事情。
            "status_before": str(manifest.get("status") or ""),
        }
    )
    block.update({"block_id": block_id, "attempts": attempts, "history": history})
    # 与 Codex 版共用同一本账的兼容视图：block_history 是 history 的别名。
    manifest["block_history"] = history
    return block, "", False


def write_blocker_note(root: Path, task_id: str, block_id: str, attempts: int, reason: str) -> Path:
    """第 2 次 fail 时落一份卡点记录，供 M1 / 用户决策（不改变 Gate 语义）。"""
    directory = root / "docs" / "BLOCKERS"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}-{block_id}.md"
    path.write_text(
        "\n".join(
            [
                f"# 卡点升级 — {task_id} / {block_id}",
                "",
                f"- 同根因失败次数：{attempts}/{MAX_BLOCK_ATTEMPTS}",
                f"- 最近一次原因：{reason or '(未填)'}",
                f"- 时间：{now_iso()}",
                "",
                "## 为什么停在这里",
                "",
                f"同一根因已 fail {attempts} 次，按三级阶梯必须**换真正不同的负责窗口**。",
                "",
                "## 下一步（由 M1 / 用户决定）",
                "",
                "- [ ] `reassign` 换一个合格的新负责人窗（DSH 上通常需要用户新开窗口）",
                "- [ ] 宿主没有合格替换者时：写 `NO_ELIGIBLE_REPLACEMENT_WORKER`，暂停本任务",
                "- [ ] 缩小范围 / 砍需求",
                "",
                f"> 在此之前，该负责人禁止继续对同一根因盲改。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def block_budget_line(manifest: Dict[str, Any]) -> str:
    block = manifest.get("block_attempts")
    handoffs = manifest.get("assignment_history")
    chain = ""
    if isinstance(handoffs, list) and handoffs:
        chain = "；交接 " + " → ".join(
            str(item.get("to")) for item in handoffs if isinstance(item, dict)
        )
    if not isinstance(block, dict) or not block.get("block_id"):
        return (
            f"卡点上限 {MAX_BLOCK_ATTEMPTS} 次（第 2 次换执行者，第 3 次硬停）；"
            f"当前 0/{MAX_BLOCK_ATTEMPTS}{chain}"
        )
    attempts = max(0, int(block.get("attempts", 0) or 0))
    remaining = MAX_BLOCK_ATTEMPTS - attempts
    return (
        f"当前卡点 {block.get('block_id')}: {attempts}/{MAX_BLOCK_ATTEMPTS}"
        f"（剩余 {remaining} 次；"
        f"{BLOCK_REPLACEMENT_HINT if attempts >= 1 else '第 1 次 fail 先复用并复述打回项'}）{chain}"
    )


def require_task_id(task_id: str) -> None:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError(
            "task_id must contain only letters, digits, '.', '_' or '-'; "
            "it must not start with punctuation"
        )


def valid_window(value: Any) -> bool:
    return isinstance(value, str) and bool(WINDOW_RE.fullmatch(value))


def gate_owned_paths(task_id: str) -> set[str]:
    return {
        normalized_path(f".task/{task_id}/manifest.json"),
        normalized_path(f".task/{task_id}/rerun.json"),
        normalized_path(f".task/{task_id}/verify-report.json"),
        # 工具自己写的派工基线：不是工人改动，不该要求申报。
        normalized_path(f".task/{task_id}/contract.json"),
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
        data = read_json(path)
    except ValueError as exc:
        return None, [str(exc)]
    if schema_mismatch(data):
        return None, [
            unsupported_schema_error(
                normalized_path(str(path.relative_to(root))), data
            )
        ]
    return data, []


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

    read_budget = manifest.get("read_budget")
    if read_budget is not None:
        if not isinstance(read_budget, list):
            errors.append(f"{task_id}: read_budget must be a list when present")
        else:
            for index, item in enumerate(read_budget, start=1):
                if not isinstance(item, dict):
                    errors.append(f"{task_id}: read_budget[{index}] must be an object")
                    continue
                path = item.get("path")
                span = item.get("lines") or item.get("range")
                anchor = item.get("anchor")
                if not isinstance(path, str) or not path.strip():
                    errors.append(f"{task_id}: read_budget[{index}] missing path")
                if anchor is not None and (
                    not isinstance(anchor, str) or not anchor.strip()
                ):
                    errors.append(
                        f"{task_id}: read_budget[{index}] anchor must be a non-empty string"
                    )
                if not isinstance(span, str) or not span.strip():
                    errors.append(f"{task_id}: read_budget[{index}] missing lines/range")
                elif span.strip().lower() != "full" and not re.fullmatch(
                    r"\d+\s*-\s*\d+", span.strip()
                ):
                    errors.append(
                        f"{task_id}: read_budget[{index}] lines must be 'N-M' or 'full', "
                        f"got {span!r}"
                    )

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
    """Read every host evidence ledger; a missing ledger contributes nothing."""
    entries: List[Dict[str, Any]] = []
    for path in (hook_log_path(root), dsh_ledger_path(root)):
        if not path.exists():
            continue
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
    dsh_ends = [
        item for item in ends if str(item.get("source") or "") in DSH_SOURCES
    ]
    if dsh_ends:
        return [
            "HOOK_EVIDENCE_MISSING: dsh-runs.jsonl has no complete start/end pair "
            "from dsh-stop or dsh-subagent-end"
        ]
    if not entries and not hook_log_path(root).exists() and not dsh_ledger_path(root).exists():
        return [
            "HOOK_EVIDENCE_MISSING: hook_supervision=true but no hook-runs.jsonl "
            "and no dsh-runs.jsonl"
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
    # 结构错误：tests[] / evidence[] 里的字段写错必须被指出，不能静默丢证据。
    errors.extend(validate_report_shape(report, task_id))
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


def describe_unknown_fields(
    item: Any,
    allowed: Dict[str, str],
    *,
    where: str,
) -> List[str]:
    """列出条目里不该出现的字段，并给出正确写法。

    方向文件 §3「结构错误」：必需命令字段**缺失或拼错**导致证据无法解释时，
    要指出文件、条目、字段和正确写法，**不能静默跳过**。
    合法可选元数据不在此列（用 `allowed` 白名单表达）。
    """
    if not isinstance(item, dict):
        return []
    problems: List[str] = []
    for key in item:
        if key in allowed:
            continue
        hint = nearest_field_hint(str(key), allowed)
        problems.append(
            f"{UNKNOWN_FIELD}: {where} 出现未知字段 {key!r}"
            + (f"；是否想写 {hint}？" if hint else "")
            + f"（允许的字段：{', '.join(sorted(allowed))}）"
        )
    return problems


def nearest_field_hint(key: str, allowed: Dict[str, str]) -> str:
    """相似度最高的合法字段名（只做提示，不改写数据）。"""
    lowered = key.strip().lower()
    best = ""
    best_score = 0.0
    for candidate in allowed:
        score = difflib.SequenceMatcher(None, lowered, candidate.lower()).ratio()
        if key in allowed[candidate] or candidate in key:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.5 else ""


def validate_report_shape(report: Any, task_id: str) -> List[str]:
    """解析 worker-report 的 `tests[]` / `evidence[]`，格式错误要能被定位。"""
    if not isinstance(report, dict):
        return []
    errors: List[str] = []
    where = f"{task_id} worker-report.json"
    tests = report.get("tests")
    if isinstance(tests, list):
        for index, item in enumerate(tests, start=1):
            errors.extend(
                describe_unknown_fields(
                    item, TEST_ITEM_FIELDS, where=f"{where} tests[{index}]"
                )
            )
            if isinstance(item, dict) and not str(item.get("command") or "").strip():
                errors.append(
                    f"{UNKNOWN_FIELD}: {where} tests[{index}] 缺少必需字段 'command'"
                    "（命令必须写在 command 里才会被重放；说明文字放 note）"
                )
    evidence = report.get("evidence")
    if isinstance(evidence, list):
        for index, item in enumerate(evidence, start=1):
            errors.extend(
                describe_unknown_fields(
                    item, EVIDENCE_ITEM_FIELDS, where=f"{where} evidence[{index}]"
                )
            )
    return errors


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


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def content_fingerprint(root: Path, paths: List[str]) -> Dict[str, str]:
    """按**内容**记录工作区指纹；删除的文件记为 absent。"""
    fingerprint: Dict[str, str] = {}
    for raw in sorted(set(paths)):
        normalized = normalized_path(raw)
        candidate = root / normalized
        fingerprint[normalized] = sha256_file(candidate) if candidate.is_file() else "absent"
    return fingerprint


def recorded_fingerprint(root: Path, task_id: str) -> Dict[str, str]:
    """上次真跑 Gate 时记录的**内容**指纹（来自 rerun.json）。"""
    path = task_dir(root, task_id) / "rerun.json"
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except ValueError:
        return {}
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    return {normalized_path(key): str(value) for key, value in files.items()}


def fingerprint_sources(diff_files: List[str], report: Optional[Dict[str, Any]]) -> List[str]:
    """指纹覆盖"工作区改动"与"报告自称改动"的并集。

    只看 git diff 不够：非 git 项目（或改动还没进 diff）会漏掉真正的过期证据。
    """
    paths = list(diff_files)
    if isinstance(report, dict) and isinstance(report.get("changed_files"), list):
        paths.extend(str(value) for value in report["changed_files"] if value)
    return sorted({normalized_path(value) for value in paths if str(value).strip()})


def stale_report_paths(
    root: Path,
    report: Optional[Dict[str, Any]],
    previous: Dict[str, str],
) -> List[str]:
    """报告自称的 `changed_files` 中，自上次 Gate 之后**内容**又变过的那些。

    只用内容比对，绝不看 mtime（时间戳可能只有秒级粒度，同秒写入会假阴性）。
    `previous` 必须是**上次记录的**指纹：拿本次刚算出的指纹去比等于自己跟自己比，
    永远判不出过期。
    """
    if not isinstance(report, dict):
        return []
    changed = report.get("changed_files")
    if not isinstance(changed, list):
        return []
    if not previous:
        # 还没有基线：本次只建立基线，下次再比。
        return []
    stale: List[str] = []
    for raw in sorted({normalized_path(value) for value in changed if value}):
        candidate = root / raw
        if not candidate.is_file():
            continue
        if previous.get(raw, "absent") != sha256_file(candidate):
            stale.append(raw)
    return stale


STALE_REPORT_HINT = (
    "请先重跑验收命令并在报告里更新证据；报告只能**提示**它可能旧了，"
    "不能声称验收仍然新鲜"
)


def stale_report_message(task_id: str, stale: List[str], *, blocking: bool) -> str:
    scope = "禁止收口" if blocking else "仅提示"
    return (
        f"STALE_REPORT: {task_id} worker-report.json covers changed_files that were "
        f"modified after it was written ({scope}): {', '.join(stale)}；{STALE_REPORT_HINT}"
    )


def validate_manifest_commands(manifest: Dict[str, Any]) -> List[str]:
    """任务侧命令收口检查（不执行）。

    `requirements[].verify` 若是自然语言描述就会被忽略；只有 `verify_cmd` /
    可执行形态的 `verify` 才被收集。这里额外拦住"明显不可执行"的 verify_cmd。
    """
    errors: List[str] = []
    for requirement in manifest.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        command = requirement.get("verify_cmd")
        if not isinstance(command, str) or not command.strip():
            continue
        reason = unreplayable_reason(command.strip())
        if reason:
            errors.append(
                f"{UNREPLAYABLE_COMMAND}: requirements[{requirement.get('id', '?')}].verify_cmd "
                f"{reason}: {command.strip()}"
            )
    return errors


def _angle_bracket_placeholder(command: str) -> bool:
    """`<...>` 占位符，但不是 shell 重定向（`2>&1` / `> out.txt` / `< in.txt`）。"""
    for match in re.finditer(r"\s<[^<>]*>", command):
        if command[match.start() + 1] == ">":
            continue
        return True
    return False


def unreplayable_reason(command: str) -> str:
    """返回不可重放的原因；空字符串表示"形态上可重放"。

    这是**只读收口检查**：只证明它不可执行，不证明它可执行。因此绝不改变
    状态机、绝不推进 block 计数，也绝不因为二进制不在 PATH 而拒绝——
    合法但本机未安装的测试运行器不能算工人的错。
    """
    if not isinstance(command, str) or not command.strip():
        return "is empty"
    text = command.strip()
    if _angle_bracket_placeholder(text):
        return "contains a <...> placeholder (executing it would be a shell error)"
    for marker in ("**", "```", "…"):
        if marker in text:
            return f"contains non-command marker {marker!r}"
    if any(ord(char) > 0x2000 for char in text):
        return "contains full-width prose punctuation (not a command)"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "contains Chinese prose (not a command)"
    if text[0] in ("'", '"') and text[-1] == text[0]:
        return "is wrapped in quotes (would be recorded, not executed)"
    if not text.lower().startswith(COMMAND_PREFIXES):
        return "is not an executable command (no recognized runner prefix)"
    return ""


def task_local_command_file(root: Path, token: str) -> Optional[str]:
    """若 token 指向一个已声明却缺失的任务内文件，返回它的相对路径。"""
    normalized = normalized_path(token.strip("\"'"))
    if not normalized or normalized.startswith("-"):
        return None
    lowered = normalized.lower()
    if normalized.startswith("/") or lowered.startswith(("<", ">", "http:", "https:")):
        return None
    if normalized.startswith(".."):
        return None
    candidate = root / normalized
    if candidate.exists():
        return None
    head = normalized.split("/", 1)[0].lower()
    extension = Path(normalized).suffix.lower()
    looks_like_file = extension in {".js", ".mjs", ".cjs", ".ts", ".py", ".json", ".md", ".txt", ".html", ".css", ".pas"}
    if head == "_tools" or (looks_like_file and "/" in normalized):
        return normalized
    return None


def unreplayable_command_errors(root: Path, commands: List[str]) -> List[str]:
    """只做**形状**预检（占位符/说明文字/无运行器前缀）。

    "文件不存在"不在这里判：那是**事实检查**，且要区分阶段（派工允许、
    收口要求），由 `required_acceptance_scripts()` 单独处理。
    """
    errors: List[str] = []
    for command in commands:
        reason = unreplayable_reason(command)
        if reason:
            errors.append(f"{UNREPLAYABLE_COMMAND}: tests[].command {reason}: {command}")
    return errors


def missing_command_files(root: Path, command: str) -> List[str]:
    """命令里引用的、属于本任务范围但当前不存在的文件。"""
    return [
        found
        for found in (
            task_local_command_file(root, token) for token in COMMAND_FILE_RE.findall(command)
        )
        if found
    ]


def required_acceptance_scripts(
    root: Path, commands: List[str], manifest: Dict[str, Any]
) -> List[str]:
    """命令引用、**任务已声明会交付**、但当前不存在的脚本。

    这是**事实检查**，不是"凭命令外形猜测试质量"：

    - 只认本任务范围内的脚本（`_tools/` 或带扩展名的相对路径）；
    - 必须在 `allowed_paths` 内，**或**已被 `worker-report.changed_files` 申报
      —— 声明过要交付却不存在，才是"必需文件缺失"；否则可能只是别的任务的路径，
      不能据此判本任务失败。
    """
    report: Optional[Dict[str, Any]] = None
    report_path = task_dir(root, manifest.get("task_id") or "") / "worker-report.json"
    if report_path.is_file():
        try:
            report = read_json(report_path)
        except ValueError:
            report = None
    declared: set[str] = set()
    if isinstance(report, dict) and isinstance(report.get("changed_files"), list):
        declared = {normalized_path(value) for value in report["changed_files"] if value}
    allowed = manifest.get("allowed_paths")
    allowed_list = allowed if isinstance(allowed, list) else []

    missing: List[str] = []
    for command in commands:
        for relative in missing_command_files(root, command):
            normalized = normalized_path(relative)
            if normalized in declared or path_allowed(normalized, allowed_list):
                missing.append(normalized)
    return sorted(set(missing))


def contract_path(root: Path, task_id: str) -> Path:
    return task_dir(root, task_id) / "contract.json"


def contract_baseline(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """派工基线的**内容**快照：需求引用 + 参与验收的字段 + allowed_paths + risk。

    方向文件 §6.5：独立 `contract.json` 存基线，**不作为第二套任务状态**；
    可检测误改，**不宣称防作弊**（放在工人可写的位置，工具只是对比，不是权限隔离）。

    0.36 修正（Codex 审阅问题 4）：必须保存**原需求文字**，并覆盖**所有参与验收的
    字段**（`verify` 与 `verify_cmd` 都要），否则"把原标准交给验收者"是空话，
    而且只改 `verify` 会被漏检。
    """
    requirements = manifest.get("requirements")
    normalized_requirements: List[Dict[str, Any]] = []
    if isinstance(requirements, list):
        for item in requirements:
            if not isinstance(item, dict):
                continue
            normalized_requirements.append(
                {
                    "id": str(item.get("id") or ""),
                    "text": str(item.get("text") or ""),
                    "verify": str(item.get("verify") or ""),
                    "verify_cmd": str(item.get("verify_cmd") or ""),
                }
            )
    allowed = manifest.get("allowed_paths")
    return {
        "requirements": normalized_requirements,
        "allowed_paths": sorted(
            normalized_path(value) for value in allowed if str(value).strip()
        )
        if isinstance(allowed, list)
        else [],
        "risk": str(manifest.get("risk") or ""),
        "source_refs": [
            {
                "id": str(ref.get("id") or ""),
                "text": str(ref.get("text") or ""),
                "maps_to": sorted(str(m) for m in (ref.get("maps_to") or [])),
            }
            for ref in (manifest.get("source_refs") or [])
            if isinstance(ref, dict)
        ],
    }


def validate_dispatch_ready(
    root: Path,
    task_id: str,
    manifest: Dict[str, Any],
    round_data: Optional[Dict[str, Any]] = None,
    *,
    require_baseline: bool = True,
    strict_routing: bool = True,
) -> List[str]:
    """**正式派工前提**的唯一实现（`gate --dispatch` 与 `brief`/`packet` 共用）。

    Codex rev2 Q1 要求：默认派工应复用 worker brief/packet，由**同一套校验**决定
    能不能派工；不能只给 `--dispatch` 补检查，而默认派工仍可跳过。

    顺序（与 Codex 给的一致）：任务身份 → 需求 → **来源映射** → 边界 → 验证策略
    → 本轮路由 → 通过后才允许保存基线 / 输出正式简报。

    `strict_routing=False` 用于**恢复期重新出简报**：此时任务已经派工过，路由可能
    正在调整（例如换人后 `round.tasks` 与 owner 短暂不一致）。路由问题降级为提示，
    避免"因旧状态存在而永久禁止必要修复"——但**首次派工仍然严格**。
    """
    errors: List[str] = []
    if not contract_ready(manifest):
        errors.append(
            f"DISPATCH_NOT_READY: {task_id} 的 requirements / allowed_paths "
            "还没填完（或仍是模板占位文字），不能派工。"
        )
        return errors
    # 身份、必需字段与边界
    errors.extend(validate_manifest(manifest, task_id))
    # **来源映射**：这是上一版漏掉的一环（Codex rev2 Q1 反例：source_refs=[] 仍通过）
    errors.extend(validate_requirement_coverage(manifest, task_id))
    # 验证策略
    policy = verification_policy(manifest)
    if policy["conflict"]:
        errors.append(policy["conflict"])
    # 本轮路由（owner / verifier）
    if round_data is not None:
        routing_errors = validate_assignments(
            root,
            round_data,
            task_id,
            manifest,
            required=bool(policy["independent_verification"]),
            worker_window=task_worker_route(round_data, task_id) or str(manifest.get("owner") or ""),
        )
        if routing_errors and not strict_routing:
            for error in routing_errors:
                print(f"NOTE 派工路由提示（恢复期不阻断）: {error}")
        else:
            errors.extend(routing_errors)
    if errors:
        return errors
    if require_baseline:
        _, boot_error = ensure_contract(root, task_id, manifest, "taskctl")
        if boot_error:
            errors.append(boot_error)
            return errors
        errors.extend(mark_baseline_created(root, task_id, manifest))
    return errors


def mark_baseline_created(root: Path, task_id: str, manifest: Dict[str, Any]) -> List[str]:
    """在 manifest 里留一个**最小标记**："本项目已在新版派工过、当时有基线"。

    Codex rev2 Q4 要求：不能用"文件现在不存在"推定"过去从未存在"——历史任务与
    "新版已派工但基线丢失"必须区分开，否则缺失会被静默当成历史例外。
    """
    stored, status = load_contract(root, task_id)
    if stored is None:
        return []
    baseline_hash = hashlib.sha256(
        json.dumps(stored.get("baseline"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    marker = manifest.get("contract_baseline")
    if isinstance(marker, dict) and marker.get("hash") == baseline_hash:
        return []
    manifest["contract_baseline"] = {
        "at": now_iso(),
        "hash": baseline_hash,
        "schema_version": DSH_WRITES_SCHEMA,
    }
    try:
        write_json(task_dir(root, task_id) / "manifest.json", manifest)
    except OSError as exc:
        return [f"CONTRACT_BASELINE_MARK_FAILED: {task_id}: {exc}"]
    return []


def contract_ready(manifest: Dict[str, Any]) -> bool:
    """需求是否已填好、可以据此建立派工基线。

    `init` 只生成**待规划模板**（占位文字），此时不能冻结基线，
    否则正常填写需求会被判成"改验收标准"（Codex 审阅问题 3）。
    """
    requirements = manifest.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return False
    allowed = manifest.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        return False
    for item in requirements:
        if not isinstance(item, dict):
            return False
        if not str(item.get("id") or "").strip():
            return False
        if not str(item.get("text") or "").strip():
            return False
        placeholder = str(item.get("text") or "")
        if "请由 M1" in placeholder or "请填写" in placeholder:
            return False
        verify = str(item.get("verify") or "").strip()
        verify_cmd = str(item.get("verify_cmd") or "").strip()
        if not verify and not verify_cmd:
            return False
        if "请填写验证方法" in verify:
            return False
    return True


def ensure_contract(
    root: Path, task_id: str, manifest: Dict[str, Any], actor: str
) -> Tuple[Optional[Path], str]:
    """按需生成基线。**只在需求已就绪时**建立，且只在文件缺失时建立。

    Returns (path, error)：error 非空表示基线应建但建立失败（调用方不得静默继续）。
    """
    path = contract_path(root, task_id)
    if path.is_file():
        return path, ""
    # **不得用当前标准重建丢失的基线**（Codex rev3 Q4'）：只要 manifest 里已经记录过
    # "这个任务建立过基线"，文件就不见了 = 基线丢失，必须报错；只有**首次**合法派工
    # 才允许创建。恢复只能从可信来源取回原始基线，或走明确审查。
    marker = manifest.get("contract_baseline")
    if isinstance(marker, dict) and marker.get("hash"):
        return None, (
            f"ACCEPTANCE_BASELINE_LOST: {task_id} 曾建立派工基线"
            f"（记录于 manifest.contract_baseline，hash={marker.get('hash')}），"
            "但 contract.json 现在不存在。**拒绝用当前标准重建**——"
            "请从可信备份恢复原始基线，或按明确审查处理。"
        )
    if not contract_ready(manifest):
        return None, ""
    try:
        write_json(
            path,
            {
                "task_id": task_id,
                "created_at": now_iso(),
                "created_by": actor,
                "note": (
                    "工具自动生成的派工基线；用于检测验收标准被误改，不是权限隔离。"
                    "它保存原需求文字与参与验收的字段，供验收者对照。"
                ),
                "baseline": contract_baseline(manifest),
                "accepted_changes": [],
            },
        )
    except OSError as exc:
        return None, f"CONTRACT_WRITE_FAILED: 无法写入 {path.name}: {exc}"
    return path, ""


def load_contract(root: Path, task_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """读取基线。**缺失或损坏必须显式说明**，不能静默当成"没有变化"。"""
    path = contract_path(root, task_id)
    if not path.is_file():
        return None, "NO_BASELINE"
    try:
        stored = read_json(path)
    except ValueError:
        return None, "BASELINE_CORRUPT"
    baseline = stored.get("baseline")
    if not isinstance(baseline, dict):
        return None, "BASELINE_CORRUPT"
    return stored, ""


def contract_diffs(
    root: Path, task_id: str, manifest: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], str]:
    """对比当前 manifest 与派工基线，返回**结构化差异**与基线状态。

    比较的是**所有参与验收的字段**（含 `verify`）；只描述变化，不断定放宽。
    """
    stored, status = load_contract(root, task_id)
    if stored is None:
        return [], status
    baseline = stored["baseline"]
    current = contract_baseline(manifest)
    diffs: List[Dict[str, Any]] = []
    if baseline.get("allowed_paths") != current.get("allowed_paths"):
        diffs.append(
            {
                "field": "allowed_paths",
                "before": baseline.get("allowed_paths") or [],
                "after": current.get("allowed_paths") or [],
            }
        )
    if baseline.get("risk") != current.get("risk"):
        diffs.append({"field": "risk", "before": baseline.get("risk"), "after": current.get("risk")})
    before_reqs = {
        item.get("id"): item for item in (baseline.get("requirements") or []) if isinstance(item, dict)
    }
    after_reqs = {item.get("id"): item for item in current["requirements"]}
    for requirement_id in sorted(set(before_reqs) | set(after_reqs), key=str):
        before = before_reqs.get(requirement_id) or {}
        after = after_reqs.get(requirement_id) or {}
        for field in ("text", "verify", "verify_cmd"):
            if str(before.get(field) or "") != str(after.get(field) or ""):
                diffs.append(
                    {
                        "field": f"requirements[{requirement_id}].{field}",
                        "before": before.get(field),
                        "after": after.get(field),
                    }
                )
    if baseline.get("source_refs") != current.get("source_refs"):
        diffs.append(
            {
                "field": "source_refs",
                "before": baseline.get("source_refs") or [],
                "after": current.get("source_refs") or [],
            }
        )
    return diffs, ""


def describe_diffs(diffs: List[Dict[str, Any]]) -> str:
    return "; ".join(
        f"{item['field']}: {item.get('before')!r} -> {item.get('after')!r}" for item in diffs
    )


def acceptance_key(diffs: List[Dict[str, Any]]) -> str:
    payload = json.dumps(
        [[item["field"], item.get("before"), item.get("after")] for item in diffs],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_contract(
    root: Path,
    task_id: str,
    manifest: Dict[str, Any],
    *,
    required: bool = False,
) -> List[str]:
    """标准变更：**提示变化，确认后才收口**（方向文件 §3「标准改变」）。

    基线的四种情形必须分开（Codex rev2 Q4）——不能用"文件现在不存在"推定
    "过去从未存在"，也不能在收口时补当前快照冒充"原标准"：

    | 情形 | 处理 |
    |---|---|
    | 可确认的历史任务（从未在新版派工过） | **只在收口时提示一次**，不阻断、不重复制造噪声 |
    | 新版派工过（manifest 有 `contract_baseline` 标记）但基线消失 | **阻断**：恢复可信备份或按明确审查处理 |
    | 基线损坏/结构不合法 | **阻断**并明确报错，不能当无变化 |
    | 正常 | 照常对比 |
    """
    stored, status = load_contract(root, task_id)
    if status == "NO_BASELINE":
        marker = manifest.get("contract_baseline")
        if isinstance(marker, dict) and marker.get("hash"):
            # 新版派工过、当时有基线 → 现在没了，不能当历史例外。
            return [
                "ACCEPTANCE_BASELINE_LOST: "
                f"{task_id} 在新版派工点建立过基线（记录于 manifest.contract_baseline，"
                f"hash={marker.get('hash')}），但 contract.json 现在不存在。"
                "不能把当前标准当成「原标准」重建；请恢复可信备份，"
                "或按明确审查处理后再收口。"
            ]
        # 可确认的历史任务：只在收口时提示**一次**，不阻断、不每次 gate 都吵。
        if required:
            print(
                "ACCEPTANCE_BASELINE_MISSING: "
                f"{task_id} 是基线机制之前派工的历史任务，从未建立派工基线；"
                "本次**未**对照派工原标准，按其余现有验收策略查收。"
            )
        return []
    if status == "BASELINE_CORRUPT":
        return [
            f"ACCEPTANCE_BASELINE_CORRUPT: {task_id} 的 contract.json 缺失或结构不合法；"
            "不能当作「没有变化」，请恢复可信基线（不要在未核对的情况下覆盖）。"
        ]
    diffs, _ = contract_diffs(root, task_id, manifest)
    if not diffs:
        return []
    accepted = stored.get("accepted_changes")
    key = acceptance_key(diffs)
    if isinstance(accepted, list) and any(
        isinstance(item, dict) and item.get("key") == key for item in accepted
    ):
        return []
    original = stored.get("baseline") or {}
    original_requirements = "; ".join(
        f"{item.get('id')}: {item.get('text')}（verify={item.get('verify') or item.get('verify_cmd')}）"
        for item in (original.get("requirements") or [])
        if isinstance(item, dict)
    )
    return [
        "ACCEPTANCE_CHANGED: "
        f"{task_id} 的派工基线被改动（{describe_diffs(diffs)}）。"
        f"原需求与原标准：{original_requirements or '(基线未记录)'}；"
        "变化不自动等于放宽：请由验收者对照**原需求**说明新标准是否等价或更严，"
        "再由 M1 用 `adjudicate - accept`（记录理由）后才收口；"
        "不得只重跑修改后的命令就算通过。"
    ]


def verifier_judgment_evidence(
    root: Path, task_id: str, manifest: Dict[str, Any], key: str
) -> Tuple[Optional[Dict[str, Any]], str]:
    """检查"验收者是否**针对当前这次变化**作过判断"。

    Codex rev3 Q2' 的三条要求（全部机械可查）：

    1. **不用 mtime 兜底**——复制/重存/恢复都会改时间，不代表重新审查过原标准。
       必须用 `contract_key` 明确绑定**当前变化指纹**。
    2. 报告**归属与身份**必须正确：`task_id` 对应本任务、`reviewer` 是合法窗号、
       与实现者分离；需要独立验收时还必须是被指派的 verifier。
    3. 要有**针对本次变化的明确审查结论**（`standard_verdict`），而不是一个泛泛的 pass。

    报告的 `result`（功能验收结论）与本次变更审查是两件事：功能 fail 不能当完工，
    变更接受也必须有自己的结论。`contract_key` 与差异由 `brief`/`packet` 自动给出。
    """
    path = task_dir(root, task_id) / "verify-report.json"
    if not path.is_file():
        return None, (
            "ADJUDICATE_NO_EVIDENCE: 没有 verify-report.json。"
            "需要独立审查的变化不能由 M1 自己声明「已复核」——请先由验收者"
            "复查本次变化并在报告里写 `contract_key` 与 `standard_verdict`。"
        )
    try:
        report = read_json(path)
    except ValueError as exc:
        return None, f"ADJUDICATE_NO_EVIDENCE: verify-report.json 不可解析：{exc}"

    report_task = str(report.get("task_id") or "")
    if report_task != task_id:
        return None, (
            f"ADJUDICATE_WRONG_REPORT: verify-report.task_id={report_task!r} 不是本任务"
            f"（{task_id!r}）——不接受归属错误的材料。"
        )
    reviewer = str(report.get("reviewer") or "")
    if not valid_window(reviewer):
        return None, (
            f"ADJUDICATE_WRONG_REVIEWER: verify-report.reviewer={reviewer!r} 不是合法窗号"
            "（M1-M10 或 Cn）——不接受身份不明的审查。"
        )
    owner = str(manifest.get("owner") or "")
    if reviewer == owner:
        return None, (
            f"ADJUDICATE_NO_EVIDENCE: verify-report.reviewer={reviewer!r} 就是实现者"
            f"（owner={owner!r}）；实现者不能自己审查自己的标准变化。"
        )
    # 需要独立验收时，审查者必须是被指派的那一位（复用既有路由规则）
    policy = verification_policy(manifest)
    if policy["independent_verification"]:
        try:
            round_data = load_round(root)
        except ValueError:
            round_data = None
        assigned = task_verifier_route(round_data, task_id)
        if assigned and reviewer != assigned:
            return None, (
                f"ADJUDICATE_WRONG_REVIEWER: reviewer={reviewer!r} 不是本轮指派的 verifier"
                f"（{assigned!r}）——验收路由必须一致。"
            )

    report_key = str(report.get("contract_key") or "")
    if not report_key:
        return None, (
            "ADJUDICATE_NO_EVIDENCE: 报告没有 `contract_key`，无法证明它审查的是**本次**变化"
            "（不接受按文件时间推定）。请在报告里写 `contract_key`（本值见下）与 "
            "`standard_verdict`。"
        )
    if report_key != key:
        return None, (
            f"ADJUDICATE_STALE_EVIDENCE: 报告的 contract_key={report_key} 与当前变化"
            f"（{key}）不一致——旧报告不覆盖新变化，请重新审查本次变化。"
        )
    verdict = str(report.get("standard_verdict") or "").strip().lower()
    if verdict not in {"equivalent", "stricter", "accepted"}:
        return None, (
            f"ADJUDICATE_NO_VERDICT: 报告缺少针对本次变化的明确审查结论"
            f"（`standard_verdict` 需为 equivalent / stricter / accepted，当前={verdict!r}）。"
            "功能验收的 result 不能代替变更审查结论。"
        )
    evidence = {
        "reviewer": reviewer,
        "report": normalized_path(f".task/{task_id}/verify-report.json"),
        "report_hash": sha256_file(path),
        "contract_key": report_key,
        "standard_verdict": verdict,
        "functional_result": str(report.get("result") or ""),
    }
    return evidence, ""


def cmd_adjudicate(args: argparse.Namespace, root: Path) -> int:
    """M1 对"验收标准变更"的显式接受（不新增角色，只落一条可审计记录）。

    必须带**验收者的实际判断依据**：谁判的、依据哪份证据、针对哪次变化
    （Codex rev2 Q2）。`--origin` 用来记录变化的来由（例如"用户新增需求"），
    以便区分"放宽"与"需求变更"。
    """
    if args.actor != "M1":
        print("ADJUDICATE_FAIL: requires actor=M1")
        return 1
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("ADJUDICATE_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.kind != "accept":
        print(f"ADJUDICATE_FAIL: unsupported kind {args.kind!r}")
        return 1
    stored, status = load_contract(root, args.task_id)
    if stored is None:
        print(f"ADJUDICATE_FAIL: {status}; 没有基线可对照")
        return 1
    diffs, _ = contract_diffs(root, args.task_id, manifest)
    if not diffs:
        print("ADJUDICATE_NOOP: 当前没有标准变化可接受")
        return 0
    key = acceptance_key(diffs)
    evidence, evidence_error = verifier_judgment_evidence(root, args.task_id, manifest, key)
    if evidence is None:
        print(evidence_error)
        print(f"- 当前变化指纹（请写进 verify-report.contract_key）: {key}")
        print(f"- 当前变化: {describe_diffs(diffs)}")
        return 1
    record = {
        "key": key,
        "at": now_iso(),
        "by": args.actor,
        "reason": args.reason,
        "origin": str(getattr(args, "origin", "") or "unspecified"),
        "diffs": diffs,
        "original_requirements": (stored.get("baseline") or {}).get("requirements") or [],
        "judgment": evidence,
    }
    stored.setdefault("accepted_changes", [])
    if not isinstance(stored["accepted_changes"], list):
        stored["accepted_changes"] = []
    stored["accepted_changes"].append(record)
    write_json(contract_path(root, args.task_id), stored)
    print(f"ACCEPTANCE_ACCEPTED {args.task_id} by {args.actor}")
    print(f"- 变化: {describe_diffs(diffs)}")
    print(f"- 来由: {record['origin']}")
    print(f"- 判断者: {evidence['reviewer']}（依据 {evidence['report']}）")
    print(f"- 理由: {args.reason}")
    return 0


def current_round_task_ids(round_data: Optional[Dict[str, Any]]) -> List[str]:
    """**本轮**实际挂的任务（`round.tasks` 的值）。

    越界判据必须限定本轮：历史 done 任务的 `allowed_paths` 不能永久替本轮授权
    （Codex 审阅问题 5 反例 A）。
    """
    if not isinstance(round_data, dict):
        return []
    tasks = round_data.get("tasks")
    if not isinstance(tasks, dict):
        return []
    ids: List[str] = []
    for values in tasks.values():
        if isinstance(values, list):
            ids.extend(str(value) for value in values if value)
        elif isinstance(values, str) and values:
            ids.append(values)
    return list(dict.fromkeys(ids))


def all_authorized_paths(round_data: Optional[Dict[str, Any]], root: Path) -> List[str]:
    """**本轮**所有任务已授权路径的并集（整轮越界判据）。"""
    authorized: List[str] = []
    for task_id in current_round_task_ids(round_data):
        manifest_file = task_dir(root, task_id) / "manifest.json"
        if not manifest_file.is_file():
            continue
        try:
            manifest = read_json(manifest_file)
        except ValueError:
            continue
        allowed = manifest.get("allowed_paths")
        if isinstance(allowed, list):
            authorized.extend(str(value) for value in allowed if str(value).strip())
    if isinstance(round_data, dict):
        extra = round_data.get("authorized_paths")
        if isinstance(extra, list):
            authorized.extend(str(value) for value in extra if str(value).strip())
    return sorted({normalized_path(value) for value in authorized})


def round_baseline_fingerprint(root: Path, round_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """开轮时的**工作区内容基线**（文件 → 内容哈希）。

    `round-init` 时记录。用它区分"用户开轮前就有的改动"与"本轮新造成的改动"：
    内容与基线相同的路径不算本轮越界；之后**再次被改**（内容变化）仍要判
    （Codex 审阅问题 5：既有文件后来又被改，不能因为"开轮前已脏"就全部放过）。
    """
    stored = round_data.get("workspace_baseline") if isinstance(round_data, dict) else None
    return stored if isinstance(stored, dict) else {}


def record_round_baseline(root: Path) -> Dict[str, str]:
    git_status, diff_files = git_changed_files(root)
    if git_status != "ok":
        return {}
    return content_fingerprint(root, diff_files)


def process_owned_paths() -> set[str]:
    """流程自有产物：不该被算成任何任务的越界改动。"""
    return {
        ".task",
        "docs/MIGRATE-REPORT.md",
        "docs/TASK-STATUS.md",
    }


def round_overrun_files(
    root: Path,
    round_data: Optional[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    """整轮改动中"无法解释"的文件：既不在任何任务授权路径内，也不是流程产物。

    Returns (unexplained, authorized_outside_this_scope)。
    方向文件 §6.6：先做到**可靠的整轮越界识别**；任务归属不确定时提示并协调，
    不能用当前 HEAD diff 简单归罪某个工人。
    """
    git_status, diff_files = git_changed_files(root)
    if git_status != "ok":
        return [], []
    authorized = all_authorized_paths(round_data, root)
    owned = process_owned_paths()
    baseline = round_baseline_fingerprint(root, round_data)
    unexplained: List[str] = []
    for raw in diff_files:
        path = normalized_path(raw)
        if any(path == prefix or path.startswith(prefix + "/") for prefix in owned):
            continue
        # 门禁自有产物（rerun.json / verify-report.json 等）
        if path.startswith(".task/"):
            continue
        if path_allowed(path, authorized):
            continue
        # 开轮前就存在、且**内容未变**的改动属于用户既有改动，不算本轮越界。
        # 但内容若与开轮基线不同（被再次修改），仍然要判。
        if baseline:
            recorded = baseline.get(path)
            candidate = root / path
            if recorded is not None and candidate.is_file() and recorded == sha256_file(candidate):
                continue
        unexplained.append(path)
    return sorted(set(unexplained)), authorized


def validate_round_overrun(
    root: Path,
    round_data: Optional[Dict[str, Any]],
) -> List[str]:
    """收口时阻断：整轮存在无法解释的改动（已证实的边界错误）。"""
    unexplained, _ = round_overrun_files(root, round_data)
    if not unexplained:
        return []
    return [
        "UNDECLARED_CHANGE: 本轮改动里存在**无法解释**的文件（不在任何任务的 "
        f"allowed_paths 内，也不属于流程产物）: {', '.join(unexplained)}；"
        "请确认来源后补派任务或修正 allowed_paths，再收口。"
    ]


def weak_acceptance_reason(command: str) -> str:
    """只认**明显**偏弱、且能机械识别的形态；不靠命令外观断定真实测试质量。

    方向文件 §6.7：仅辅助提示；"纯重构"也不天然只需语法检查，
    是否足够仍按需求与回归风险由验收者判断。
    """
    text = command.strip().lower()
    if re.search(r"\b(node|python3?|py)\b[^\n]*--check\b", text):
        return "只做语法解析，不执行模块"
    if re.search(r"\b(grep|findstr|select-string)\b", text):
        return "只在源码里搜索字符串"
    if re.search(r"--version\b|--help\b", text):
        return "只打印版本/帮助，不验证行为"
    if re.search(r"\btrue\b\s*$|exit\s+0\s*$|:\s*$", text):
        return "看起来是恒真命令"
    return ""


def weak_acceptance_notes(commands: List[str]) -> List[str]:
    notes: List[str] = []
    for command in commands:
        reason = weak_acceptance_reason(command)
        if reason:
            notes.append(f"WEAK_ACCEPTANCE: {reason} —— {command}")
    return notes


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


def validate_evidence_rerun(
    root: Path,
    task_id: str,
    manifest: Dict[str, Any],
    *,
    closure: bool = False,
) -> List[str]:
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
                f"{task_id}: 本任务 allowed_paths 内、但未列入 worker-report.changed_files 的改动"
                f"（且不属于门禁自有产物）: {', '.join(undeclared)}"
            )

    commands = collect_verify_commands(manifest, report)
    # 标准变更先于命令执行判定：否则被改过的命令先跑，变化就藏在别的失败后面了。
    # （基线的自动建立已在 gate() 入口完成，这里不再重复。）
    contract_errors = validate_contract(root, task_id, manifest, required=closure)
    if contract_errors:
        emit_acceptance_changed(contract_errors)
    errors.extend(contract_errors)
    # 形态错误（占位符/说明文字/无运行器前缀）无论哪个阶段都是真错误。
    shape_errors = unreplayable_command_errors(root, commands)
    if shape_errors:
        emit_unreplayable_command(shape_errors)
    errors.extend(shape_errors)
    # 必需验收脚本缺失：**事实检查**，不做任何创建/覆盖/删除。
    # 派工阶段由 `gate --basic` 不到这里；交付/收口阶段缺失即失败。
    # 判据只认"本任务范围内且已声明会交付"的脚本——不凭命令外形猜测试质量。
    missing_scripts = required_acceptance_scripts(root, commands, manifest)
    if missing_scripts:
        errors.append(
            f"MISSING_ACCEPTANCE_SCRIPT: {task_id} 的命令引用了**本任务声明要交付但不存在**"
            f"的脚本: {', '.join(missing_scripts)}；请补齐真实交付物后重跑，"
            "不要用占位文件制造验收前提。"
        )
    # 按原样执行原命令：不预先创建脚本、不替换命令、不改变被检查对象。
    command_results = [run_verify_command(root, command) for command in commands]
    for result in command_results:
        if result["exit_code"] != 0:
            errors.append(
                f"{task_id}: verify command failed (exit {result['exit_code']}): {result['command']}"
            )
    # 未提交产物进入 diff 会让"报告是否过期"失真：**只记录，不阻断、不计数**。
    fingerprint = content_fingerprint(root, fingerprint_sources(diff_files, report))
    stale = stale_report_paths(root, report, recorded_fingerprint(root, task_id))
    if stale:
        print(stale_report_message(task_id, stale, blocking=False))

    write_json(
        task_dir(root, task_id) / "rerun.json",
        {
            "task_id": task_id,
            "at": now_iso(),
            "git": git_status,
            "diff_files": diff_files,
            # 内容指纹：收口判断"报告是否已经过期"用**内容**，不用 mtime。
            # 文件系统时间戳粒度可能只有秒级，同秒写入会假阴性。
            "files": fingerprint,
            "commands": command_results,
            "stale_report": bool(stale),
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
    # 验收策略只认任务级 attempt（单调递增）。卡点计数（block_attempts）只驱动
    # 三级阶梯；它会被新根因重置，绝不能用来决定独立验收。
    attempt = max(0, int(manifest.get("attempt", 0) or 0))
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


def emit_unreplayable_command(errors: List[str]) -> None:
    if any(UNREPLAYABLE_COMMAND in str(error) for error in errors):
        print(UNREPLAYABLE_COMMAND)


def emit_stale_report(errors: List[str]) -> None:
    if any("STALE_REPORT" in str(error) for error in errors):
        print("STALE_REPORT")


def emit_unknown_field(errors: List[str]) -> None:
    if any(UNKNOWN_FIELD in str(error) for error in errors):
        print(UNKNOWN_FIELD)


def emit_acceptance_changed(errors: List[str]) -> None:
    for token in ("ACCEPTANCE_CHANGED", "ACCEPTANCE_BASELINE_MISSING", "ACCEPTANCE_BASELINE_CORRUPT"):
        if any(token in str(error) for error in errors):
            print(token)


def emit_missing_acceptance_script(errors: List[str]) -> None:
    if any("MISSING_ACCEPTANCE_SCRIPT" in str(error) for error in errors):
        print("MISSING_ACCEPTANCE_SCRIPT")


def emit_unsupported_schema(errors: List[str]) -> None:
    if any(UNSUPPORTED_SCHEMA in str(error) for error in errors):
        print(UNSUPPORTED_SCHEMA)


def emit_close_tokens(errors: List[str]) -> None:
    emit_policy_conflict(errors)
    emit_requirement_coverage(errors)
    emit_gear_violation(errors)
    emit_hook_evidence(errors)
    emit_parallel_fail(errors)
    emit_assignment_tokens(errors)
    emit_unreplayable_command(errors)
    emit_unsupported_schema(errors)
    emit_stale_report(errors)
    emit_unknown_field(errors)
    emit_acceptance_changed(errors)
    emit_missing_acceptance_script(errors)


def emit_assignment_tokens(errors: List[str]) -> None:
    for token in (
        VERIFIER_ASSIGNMENT_MISSING,
        VERIFIER_ASSIGNMENT_CONFLICT,
        WORKER_ASSIGNMENT_CONFLICT,
    ):
        if any(token in str(error) for error in errors):
            print(token)


def task_worker_route(round_data: Optional[Dict[str, Any]], task_id: str) -> str:
    """`round.tasks` 里挂该任务的窗号（实现路由）。找不到返回空。"""
    if isinstance(round_data, dict):
        tasks = round_data.get("tasks", {})
        if isinstance(tasks, dict):
            for window, values in tasks.items():
                ids = values if isinstance(values, list) else [values]
                if task_id in {str(item) for item in ids if item}:
                    return str(window)
    return ""


def task_verifier_route(round_data: Optional[Dict[str, Any]], task_id: str) -> str:
    """`round.verifier_assignments[task_id]`：独立验收路由。"""
    if isinstance(round_data, dict):
        assignments = round_data.get("verifier_assignments")
        if isinstance(assignments, dict):
            value = assignments.get(task_id)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def validate_assignments(
    root: Path,
    round_data: Dict[str, Any],
    task_id: str,
    manifest: Dict[str, Any],
    *,
    required: bool,
    worker_window: str = "",
) -> List[str]:
    """一任务两职责的一致性门禁（v0.36 对齐）。

    - 实现路由 `round.tasks[T]` 必须等于 `manifest.owner`；
    - 需要独立验收时必须有 `verifier_assignments[T]`，且它是本轮正式窗、不同于 owner/worker；
    - verify-report 的 reviewer 必须等于被指派的 verifier（不是"只要不是 worker 就放行"）。
    """
    errors: List[str] = []
    owner = str(manifest.get("owner") or "")
    expected = {str(value) for value in round_data.get("expected_windows", [])}
    route = task_worker_route(round_data, task_id)
    if route and owner and route != owner:
        errors.append(
            f"{WORKER_ASSIGNMENT_CONFLICT}: {task_id}: round.tasks routes it to {route} "
            f"but manifest.owner is {owner}"
        )
    assigned = task_verifier_route(round_data, task_id)
    if required and not assigned:
        errors.append(
            f"{VERIFIER_ASSIGNMENT_MISSING}: {task_id}: independent verification required "
            "but round.verifier_assignments has no window for it"
        )
    if assigned:
        if expected and assigned not in expected:
            errors.append(
                f"{VERIFIER_ASSIGNMENT_CONFLICT}: {task_id}: verifier {assigned} is not in "
                f"expected_windows ({', '.join(sorted(expected))})"
            )
        if owner and assigned == owner:
            errors.append(
                f"{VERIFIER_ASSIGNMENT_CONFLICT}: {task_id}: verifier {assigned} equals owner"
            )
        if worker_window and assigned == worker_window:
            errors.append(
                f"{VERIFIER_ASSIGNMENT_CONFLICT}: {task_id}: verifier {assigned} equals worker window"
            )
    if not required and assigned:
        errors.append(
            f"{VERIFIER_ASSIGNMENT_CONFLICT}: {task_id}: short close path must not assign a verifier "
            f"(got {assigned})"
        )
    # reviewer 必须等于被指派的 verifier
    report_path = task_dir(root, task_id) / "verify-report.json"
    if assigned and report_path.exists():
        try:
            report = read_json(report_path)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            reviewer = str(report.get("reviewer") or "")
            if reviewer and reviewer != assigned:
                errors.append(
                    f"{VERIFIER_ASSIGNMENT_CONFLICT}: {task_id}: verify-report.reviewer is "
                    f"{reviewer} but the assigned verifier is {assigned}"
                )
    return errors


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


def gate(
    root: Path,
    task_id: str,
    phase: str,
    *,
    closure: bool = False,
) -> List[str]:
    """`closure=True` 表示这次判定要用来**收口**，而不是日常迭代。

    `phase`：
      - `basic`（`--basic`）：只核工人材料，不重跑命令、不碰派工基线；
      - `full`（默认）：完整验收；
      - `dispatch`（`--dispatch`）：**派工点**——需求/边界/映射都填好后建立派工基线。
        基线必须在这里建立，否则"原标准"会记在需求还没写完的半成品上
        （Codex 审阅问题 3）。

    差别只在证据新鲜度与派工基线：其它规则完全相同，避免出现"收口一套、迭代一套"。
    """
    schema_errors = schema_problems(root)
    if schema_errors:
        # fail closed：schema 比本宿主新 → 只允许查看，禁止收口。
        return schema_errors
    manifest, errors = load_manifest(root, task_id)
    if manifest is None:
        return errors
    policy = verification_policy(manifest)
    if policy["conflict"]:
        return [policy["conflict"]]
    if phase == "dispatch":
        # 派工点：**与 brief/packet 共用同一套前提校验**（Codex rev2 Q1）。
        # 校验不通过就既不能保存基线，也不能输出"已准备好"。
        round_data: Optional[Dict[str, Any]] = None
        try:
            round_data = load_round(root)
        except ValueError:
            round_data = None
        dispatch_errors = validate_dispatch_ready(root, task_id, manifest, round_data)
        if dispatch_errors:
            return dispatch_errors
        print(f"CONTRACT_BASELINE_READY {task_id}")
        return []
    errors.extend(validate_manifest(manifest, task_id))
    errors.extend(validate_manifest_commands(manifest))
    errors.extend(validate_worker(root, task_id, manifest))
    if phase == "full":
        errors.extend(
            validate_verification(
                root, task_id, manifest, policy["independent_verification"]
            )
        )
        round_data: Optional[Dict[str, Any]] = None
        try:
            round_data = load_round(root)
        except ValueError:
            round_data = None
        if round_data is not None:
            errors.extend(
                validate_assignments(
                    root,
                    round_data,
                    task_id,
                    manifest,
                    required=policy["independent_verification"],
                )
            )
        errors.extend(validate_evidence_rerun(root, task_id, manifest, closure=closure))
    return errors


def round_path(root: Path) -> Path:
    return task_root(root) / "round.json"


def rounds_archive_dir(root: Path) -> Path:
    return task_root(root) / "rounds"


def archive_round_path(root: Path, round_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(round_id or "")).strip("-") or "round"
    return rounds_archive_dir(root) / f"{safe}.json"


def cmd_round_close(args: argparse.Namespace, root: Path) -> int:
    """整轮收口：全部任务 done 且审计通过，才原子归档 round.json。

    绝不自动触发，绝不归档未完成的轮。归档是**复制**到 `.task/rounds/` 后再
    删除 `.task/round.json`；任一步失败都不留下"半关"的轮。
    """
    try:
        data = read_json(round_path(root))
    except ValueError as exc:
        print(f"ROUND_CLOSE_FAIL: {exc}")
        return 1
    if schema_mismatch(data):
        print(f"ROUND_CLOSE_FAIL: {unsupported_schema_error('.task/round.json', data)}")
        return 1

    task_ids = task_ids_for_round(data)
    not_done: List[str] = []
    for task_id in task_ids:
        manifest, errors = load_manifest(root, task_id)
        if manifest is None:
            not_done.append(f"{task_id}(unreadable)")
            continue
        status = str(manifest.get("status", "pending"))
        if status != "done":
            not_done.append(f"{task_id}={status}")
    if not_done:
        print("ROUND_CLOSE_FAIL: 整轮仍有未完成任务，禁止归档")
        for item in not_done:
            print(f"- {item}")
        return 1

    data["check_requested"] = True
    write_json(round_path(root), data)
    if cmd_audit_round(root, closure=True) != 0:
        print("ROUND_CLOSE_FAIL: 审计未通过，round.json 保持原位")
        return 1

    round_id = str(data.get("round_id") or "round")
    destination = archive_round_path(root, round_id)
    if destination.exists():
        print(f"ROUND_CLOSE_FAIL: archive already exists: {destination.relative_to(root)}")
        return 1
    rounds_archive_dir(root).mkdir(parents=True, exist_ok=True)
    data["closed_at"] = now_iso()
    data["closed_by"] = args.actor
    # 先原子写归档，再删除原位文件：中途失败最多留下一份重复归档，不会丢轮。
    staged = destination.with_suffix(".json.tmp")
    write_json(staged, data)
    os.replace(staged, destination)
    round_path(root).unlink()
    print(f"ROUND_CLOSED {round_id}")
    print(f"ARCHIVED {normalized_path(str(destination.relative_to(root)))}")
    print("NEXT: taskctl.py round-init <new-round-id> ...")
    return 0


def cmd_init(args: argparse.Namespace, root: Path) -> int:
    require_task_id(args.task_id)
    directory = task_dir(root, args.task_id)
    if directory.exists():
        print(f"FAIL: task already exists: {directory}")
        return 1
    directory.mkdir(parents=True)
    (directory / "evidence").mkdir()
    manifest = {
        SCHEMA_FIELD: DSH_WRITES_SCHEMA,
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
    ensure_contract_schema(root)
    # **不在 init 建基线**：此刻只有占位模板，冻结合同会把正常填写需求判成改标准
    # （Codex 审阅问题 3）。基线在"需求已就绪"的派工点自动建立。
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
    verifier_assignments: Dict[str, str] = {}
    for assignment in getattr(args, "verifier", []) or []:
        if "=" not in assignment:
            print(f"FAIL: invalid --verifier assignment: {assignment}; use TASK_ID=WINDOW")
            return 1
        task_id, window = assignment.split("=", 1)
        if window not in tasks or not task_id:
            print(f"FAIL: --verifier must use an expected window and non-empty task id: {assignment}")
            return 1
        worker_window = next(
            (win for win, ids in tasks.items() if task_id in ids), ""
        )
        if worker_window and window == worker_window:
            print(f"FAIL: verifier {window} cannot equal the worker window of {task_id}")
            return 1
        verifier_assignments[task_id] = window
    write_json(
        path,
        {
            SCHEMA_FIELD: DSH_WRITES_SCHEMA,
            "round_id": args.round_id,
            "expected_windows": windows,
            "receipts": [],
            "window_status": {window: "pending" for window in windows},
            "tasks": tasks,
            "verifier_assignments": verifier_assignments,
            "gears": default_gears(),
            "hook_supervision": False,
            "check_requested": False,
            # 开轮时的工作区内容基线：用于区分"用户开轮前的既有改动"与"本轮新改动"
            # （Codex 审阅问题 5）。空 dict 表示当时没有 git 或没有未提交改动。
            "workspace_baseline": record_round_baseline(root),
        },
    )
    print(f"CREATED {path.relative_to(root)}")
    ensure_contract_schema(root)
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
    role = str(getattr(args, "role", "worker") or "worker")
    if role == "verifier":
        # 验收回执必须来自被指派的 verifier，否则会把 C2 记成"又一个 worker"。
        assigned = {
            str(value) for value in (data.get("verifier_assignments") or {}).values()
        }
        if args.window not in assigned:
            print(
                f"FAIL: {args.window} is not an assigned verifier in this round; "
                "a verifier receipt must come from round.verifier_assignments"
            )
            return 1
    receipts = data.setdefault("receipts_by_role", {})
    if not isinstance(receipts, dict):
        receipts = {}
        data["receipts_by_role"] = receipts
    receipts[args.window] = role
    plain = data.setdefault("receipts", [])
    if args.window not in plain:
        plain.append(args.window)
    data["last_receipt_at"] = now_iso()
    data.setdefault("window_status", {})[args.window] = (
        "verified" if role == "verifier" else "worker_done"
    )
    data.setdefault("window_status_at", {})[args.window] = data["last_receipt_at"]
    write_json(path, data)
    print(f"RECEIPT RECORDED {args.window} role={role}")
    return 0


def round_verification_started(root: Path, round_data: Dict[str, Any], task_id: str) -> bool:
    """验收是否已开始：任一窗已到 verifying/verified，或已有 verify-report。"""
    status = (round_data.get("window_status") or {}) if isinstance(round_data, dict) else {}
    if isinstance(status, dict) and any(
        str(value) in {"verifying", "verified"} for value in status.values()
    ):
        return True
    return (task_dir(root, task_id) / "verify-report.json").exists()


def cmd_assign_verifier(args: argparse.Namespace, root: Path) -> int:
    """只写独立验收路由；绝不改 owner / attempt / block_id / assignment_history。"""
    try:
        round_data = load_round(root)
    except ValueError as exc:
        print(f"RESULT FAIL\n- {exc}")
        return 1
    if round_data is None:
        print("RESULT FAIL\n- no .task/round.json to assign a verifier in")
        return 1
    if args.actor != "M1":
        print("RESULT FAIL\n- assign-verifier requires actor=M1")
        return 1
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    owner = str(manifest.get("owner") or "")
    worker_window = task_worker_route(round_data, args.task_id)
    if args.window == owner:
        print(f"RESULT FAIL\n- verifier cannot equal owner ({owner}); this is not a reassignment")
        return 1
    if worker_window and args.window == worker_window:
        print(f"RESULT FAIL\n- verifier cannot equal the worker window ({worker_window})")
        return 1
    expected = {str(value) for value in round_data.get("expected_windows", [])}
    if expected and args.window not in expected:
        print(
            f"RESULT FAIL\n- {args.window} is not in expected_windows "
            f"({', '.join(sorted(expected))})"
        )
        return 1
    if not verification_required(manifest):
        print(
            "RESULT FAIL\n- this task closes on the short path; a verifier must not be assigned "
            "(低风险短路径不得额外派 verifier)"
        )
        return 1
    assignments = round_data.get("verifier_assignments")
    if not isinstance(assignments, dict):
        assignments = {}
    assignments[args.task_id] = args.window
    round_data["verifier_assignments"] = assignments
    write_json(round_path(root), round_data)
    print(
        f"VERIFIER_ASSIGNED {args.task_id} -> {args.window} "
        f"(worker={worker_window or owner}; owner/attempt/block 未改动)"
    )
    return 0


def cmd_sync_worker_route(args: argparse.Namespace, root: Path) -> int:
    """把错误挂载的 `round.tasks` 恢复挂回 manifest.owner；不改 owner 本身。"""
    try:
        round_data = load_round(root)
    except ValueError as exc:
        print(f"RESULT FAIL\n- {exc}")
        return 1
    if round_data is None:
        print("RESULT FAIL\n- no .task/round.json")
        return 1
    if args.actor != "M1":
        print("RESULT FAIL\n- sync-worker-route requires actor=M1")
        return 1
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    owner = str(manifest.get("owner") or "")
    if not valid_window(owner):
        print(f"RESULT FAIL\n- manifest.owner is not a valid window: {owner!r}")
        return 1
    if round_verification_started(root, round_data, args.task_id):
        print(
            "RESULT FAIL\n- verification already started; the worker route must not change anymore"
        )
        return 1
    current = task_worker_route(round_data, args.task_id)
    if current == owner:
        print(f"WORKER_ROUTE_ALREADY_OK {args.task_id} -> {owner}")
        return 0
    tasks = round_data.get("tasks")
    if not isinstance(tasks, dict):
        print("RESULT FAIL\n- round.tasks must be an object")
        return 1
    for window, values in tasks.items():
        if isinstance(values, list) and args.task_id in [str(item) for item in values]:
            tasks[window] = [item for item in values if str(item) != args.task_id]
    tasks.setdefault(owner, [])
    if args.task_id not in tasks[owner]:
        tasks[owner].append(args.task_id)
    round_data["tasks"] = tasks
    write_json(round_path(root), round_data)
    print(f"WORKER_ROUTE_SYNCED {args.task_id}: {current or '(none)'} -> {owner} (owner 未改动)")
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
    if getattr(args, "dispatch", False):
        phase = "dispatch"
    elif args.basic:
        phase = "basic"
    else:
        phase = "full"
    errors = gate(root, args.task_id, phase)
    if errors:
        emit_policy_conflict(errors)
        emit_requirement_coverage(errors)
        emit_unsupported_schema(errors)
        emit_unreplayable_command(errors)
        emit_stale_report(errors)
        emit_unknown_field(errors)
        emit_acceptance_changed(errors)
        emit_missing_acceptance_script(errors)
        print("RESULT FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("RESULT PASS")
    return 0


def cmd_audit_round(
    root: Path,
    require_hook_evidence: bool = True,
    closure: bool = False,
) -> int:
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
        # 收口路径会先自行置 true，所以走到这里等于"从来没请求过审计"。
        print("AUDIT SKIP (check_requested=false)")
        return 1 if closure else 0

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
        # 例行审计：这不是错误，返回 0 只表示"还没就绪"。
        # 但**收口**时它不是通过：绝不允许把没就绪的轮当成收口依据归档。
        return 1 if closure else 0

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
        # 只有真正要归档的那次（round-close → closure=True）才把过期报告当阻断项。
        # 例行审计仍然放行并打印提示，避免一条软提示卡住整个复盘流程。
        full_errors.extend(gate(root, task_id, "full", closure=closure))
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
    # 整轮越界：已证实的边界错误 → 收口时阻断（方向文件 §3「已证实的边界错误」）。
    # 归属不确定的改动只提示，不简单归罪某个工人。
    overrun = validate_round_overrun(root, data)
    if overrun:
        print("UNDECLARED_CHANGE")
        for error in overrun:
            print(f"- {error}")
        return 1
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


def dsh_ledger_path(root: Path) -> Path:
    return task_root(root) / "dsh-runs.jsonl"


def evidence_log_path(root: Path, source: str) -> Path:
    """One ledger per host family: dsh keeps its own line budget."""
    return dsh_ledger_path(root) if source in DSH_SOURCES else hook_log_path(root)


def current_round_id(root: Path) -> str:
    path = round_path(root)
    if not path.exists():
        return ""
    try:
        data = read_json(path)
    except ValueError:
        return ""
    return str(data.get("round_id") or "")


def trim_hook_log(path: Path, cap: int = HOOK_LOG_MAX_LINES) -> None:
    if not path.exists():
        return
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) <= cap:
        return
    path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")


def append_hook_run(
    root: Path,
    *,
    run_id: str,
    phase: str,
    source: str,
    host: str,
    exit_code: Optional[int],
    audit_result: str,
    session_id: str = "",
    agent_id: str = "",
    intent: str = "audit",
) -> Path:
    path = evidence_log_path(root, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "phase": phase,
        "timestamp": now_iso(),
        "event": "subagent-stop" if source == "dsh-subagent-end" else "stop",
        "source": source,
        "host": host,
        "cwd": str(Path.cwd()),
        "project_root": str(root),
        "round_id": current_round_id(root),
        "exit_code": exit_code,
        "audit_result": audit_result,
        "command": "taskctl.py hook-audit",
        "intent": intent,
    }
    if session_id:
        entry["session_id"] = session_id
    if agent_id:
        entry["agent_id"] = agent_id
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    trim_hook_log(path, DSH_LEDGER_MAX_LINES if source in DSH_SOURCES else HOOK_LOG_MAX_LINES)
    return path


def cmd_hook_audit(
    root: Path,
    source: str,
    host: Optional[str] = None,
    session_id: str = "",
    agent_id: str = "",
    intent: str = "audit",
) -> int:
    if not task_root(root).is_dir():
        print("HOOK_AUDIT_SKIP (no .task)")
        return 0
    if host == DSH_HOST and source not in DSH_SOURCES:
        print(f"HOOK_AUDIT_FAIL: --host dsh requires a dsh-* source, got {source}")
        return 2
    # A dsh hook fires ONCE per event; there is no separate end invocation the
    # way a Cursor stop pair has. So at least one dsh event must mint the whole
    # pair itself, or every window would leave an orphan start that can never
    # pair up. `--once` is that mode: one call, complete start/end, one run_id
    # (the host session id, which is also what lets M1 tie a stop to a window).
    if source in DSH_SOURCES:
        resolved_host = DSH_HOST
        run_id = session_id or str(uuid.uuid4())
        intent = intent if intent in {"audit", "pair", "record"} else "pair"
        if intent == "record":
            path = append_hook_run(
                root,
                run_id=run_id,
                phase="start",
                source=source,
                host=resolved_host,
                exit_code=0,
                audit_result="recorded",
                session_id=session_id,
                agent_id=agent_id,
                intent=intent,
            )
            print(
                f"HOOK_RUN_RECORDED {path.relative_to(root)} run_id={run_id} "
                f"host={resolved_host} source={source} phase=start"
            )
            return 0
        append_hook_run(
            root,
            run_id=run_id,
            phase="start",
            source=source,
            host=resolved_host,
            exit_code=None,
            audit_result="pending",
            session_id=session_id,
            agent_id=agent_id,
            intent=intent,
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
            session_id=session_id,
            agent_id=agent_id,
            intent=intent,
        )
        print(
            f"HOOK_RUN_RECORDED {path.relative_to(root)} run_id={run_id} "
            f"host={resolved_host} source={source} phase=end pair=complete"
        )
        # Never propagate a gate verdict as the process exit code: the dsh Stop
        # hook reads exit 2 as "force another turn", and a not-yet-closeable
        # round is the normal state during live work.
        if exit_code not in {0, 1}:
            return 1
        return 0
    run_id = session_id or str(uuid.uuid4())
    resolved_host = host or SOURCE_HOST.get(source, "unknown")
    append_hook_run(
        root,
        run_id=run_id,
        phase="start",
        source=source,
        host=resolved_host,
        exit_code=None,
        audit_result="",
        session_id=session_id,
        agent_id=agent_id,
        intent=intent,
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
        session_id=session_id,
        agent_id=agent_id,
        intent=intent,
    )
    print(f"HOOK_RUN_RECORDED {path.relative_to(root)} run_id={run_id} host={resolved_host}")
    return exit_code


def validate_shared_file_compat(data: Any, rel: str) -> List[str]:
    """结构兼容性验证（**不写盘**）：转换后能否被当前规则理解。

    方向文件 §6.3 + Codex 审阅问题 7：不能只检查 `status`。

    两类要分开判（Codex 的原文要求）：
      - **未派工的模板留空是正常的**（`allowed_paths` / `requirements` 可以空），
        但它必须**由工具创建的骨架**——`init` 总会写 `track` / `risk` / `status_history`，
        缺这些说明它不是模板，而是**残缺文件**，不能标成兼容；
      - **已开工/已交付的任务**缺 `owner` / `allowed_paths` / `requirements` 一律阻断。
    """
    if not isinstance(data, dict):
        return [f"{rel}: 根不是 JSON 对象"]
    problems: List[str] = []
    status = str(data.get("status") or "pending")
    if status not in TASK_STATES:
        problems.append(f"{rel}: status={status!r} 不在当前规则允许的状态集里")

    is_round = "round_id" in data or "expected_windows" in data
    if is_round:
        if not isinstance(data.get("expected_windows"), list) or not data.get("expected_windows"):
            problems.append(f"{rel}: round 缺 expected_windows（结构必需）")
        return problems

    # 任务 manifest
    if not str(data.get("task_id") or "").strip():
        problems.append(f"{rel}: 缺 task_id（结构必需）")
        return problems

    missing_skeleton = [
        field
        for field in ("track", "risk", "status_history")
        if data.get(field) in (None, "")
    ]
    if missing_skeleton:
        # 不是"模板留空"，而是残缺文件：连骨架都不完整。
        problems.append(
            f"{rel}: 缺 {'、'.join(missing_skeleton)}（结构必需；工具创建的模板一定包含它们）"
        )
        return problems

    started = status not in {"pending", "paused"}
    if not str(data.get("owner") or "").strip():
        problems.append(
            f"{rel}: 缺 owner"
            + ("（该任务已开工/已交付，owner 是结构必需）" if started else "（模板可留空）")
        )
    allowed = data.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        problems.append(
            f"{rel}: 缺 allowed_paths"
            + ("（该任务已开工/已交付，边界是结构必需）" if started else "（模板可留空）")
        )
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        problems.append(
            f"{rel}: 缺 requirements"
            + ("（该任务已开工/已交付，验收需求是结构必需）" if started else "（模板可留空）")
        )
    # 未开工：只保留结构性缺项（骨架已单独判过，这里只剩 status 之外的空洞）。
    if not started:
        return [item for item in problems if "已开工" not in item]
    return problems


def upgrade_shared_file_schema(
    path: Path, rel: str, target: int
) -> Tuple[str, str, List[str], Optional[Dict[str, Any]]]:
    """**纯计算**：算出这个文件转换后的内容，不写盘。

    方向文件 §6.3 + Codex 审阅问题 6：必须在**全部输入都预检通过之后**才提交；
    失败不得留下任何部分修改。所以这里只返回"打算写成什么"，写盘由调用方统一做。
    Returns (action, detail, problems, converted_data_or_None)。
    """
    try:
        data = read_json(path)
    except ValueError as exc:
        return "failed", "", [f"{rel}: {exc}"], None
    declared = schema_version_of(data)
    if declared > target:
        return "skipped", f"schema_version={declared}", [
            f"{UNSUPPORTED_SCHEMA}: {rel} 声明 schema_version={declared} > {target}；"
            "本宿主读不懂，拒绝改写（保持只读）"
        ], None
    problems = validate_shared_file_compat(data, rel)
    if problems:
        return "failed", f"schema_version={declared}", problems, None
    if declared == target:
        return "already", f"schema_version={declared}", [], None
    converted = dict(data)
    converted[SCHEMA_FIELD] = target
    return "changed", f"schema_version={declared} -> {target}", [], converted


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

    # ---- 第一步：**只预检**，不写任何共同文件 ----
    # 方向文件 §6.3 + Codex 审阅问题 6：必须在全部输入都通过之后才提交；
    # 失败绝不留下部分修改（包括目标脚本）。
    conversions: List[Dict[str, str]] = []
    problems: List[str] = []
    pending: List[Tuple[Path, Dict[str, Any]]] = []
    targets: List[Tuple[Path, str]] = []
    round_file = round_path(root)
    if round_file.is_file():
        targets.append((round_file, ".task/round.json"))
    for directory in sorted(task_root(root).glob("TASK-*")):
        manifest_file = directory / "manifest.json"
        if manifest_file.is_file():
            targets.append((manifest_file, f".task/{directory.name}/manifest.json"))
    for path, rel in targets:
        action, detail, found, converted = upgrade_shared_file_schema(
            path, rel, DSH_WRITES_SCHEMA
        )
        conversions.append({"file": rel, "action": action, "detail": detail})
        problems.extend(found)
        if converted is not None:
            pending.append((path, converted))

    lock["conversions"] = conversions
    if problems:
        lock["status"] = "conversion_failed"
        write_json(lock_path(root), lock)
        write_migrate_report(root, lock)
        print("MIGRATE_CONVERSION_FAILED")
        for item in conversions:
            print(f"- {item['file']}: {item['action']} ({item['detail']})")
        for problem in problems:
            print(f"- {problem}")
        print(
            "NEXT 未改动任何共同文件、也未替换目标脚本；schema 契约未提交。"
            "请按上面每条修正后重跑。"
        )
        return 1

    # ---- 第二步：预检全部通过，才开始提交 ----
    # Codex rev2 §6 / rev3 补充要求：提交阶段可能中途 I/O 失败，所以先记录**每个会被
    # 改写的文件**的原始状态，失败时原样回滚。三处细节按 rev3 修正：
    #   1) 用 (原本是否存在, 原始字节) 区分"原来不存在"与"原来是空文件"——
    #      只按字节真假判断会把空的既有文件删掉；
    #   2) 回滚清单必须覆盖**所有影响兼容性/迁移结论的写入**，包括
    #      `.task/skill-lock.json` 与迁移报告，而不只是共同文件和目标脚本；
    #   3) 恢复失败要**明确列出**，不宣称全部恢复。
    write_targets: List[Path] = [destination, lock_path(root), root / MIGRATE_REPORT_REL]
    write_targets.extend(path for path, _ in pending)
    snapshot: List[Tuple[Path, bool, bytes]] = []
    for path in write_targets:
        try:
            if path.is_file():
                snapshot.append((path, True, path.read_bytes()))
            else:
                snapshot.append((path, False, b""))
        except OSError as exc:
            print(f"MIGRATE_FAIL: 无法读取 {path} 的原始内容，已中止（未做任何修改）：{exc}")
            return 1

    def _rollback(reason: str) -> None:
        restored: List[str] = []
        failed: List[str] = []
        for path, existed, original in snapshot:
            rel = normalized_path(str(path.relative_to(root)))
            try:
                if existed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
                    restored.append(rel)
                elif path.exists():
                    path.unlink()
                    restored.append(rel + "(removed)")
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
        print(f"MIGRATE_COMMIT_FAILED: {reason}")
        print(f"- 已回滚: {', '.join(restored) if restored else '(无可回滚项)'}")
        if failed:
            print("- **恢复失败（需人工处理）**: " + "; ".join(failed))
        else:
            print("- 目标脚本、共同文件、版本锁与迁移报告均已回到迁移前状态。")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        task_root(root).mkdir(parents=True, exist_ok=True)
        lock["status"] = "copied"
        print(f"COPIED {dest_rel}")
        print(f"MIGRATED {dest_rel} from multi-window-m-036 v{SKILL_VERSION}")

        for path, converted in pending:
            write_json(path, converted)

        # 顺序要紧：先把契约写进磁盘，再把内存里的 lock 写回（否则会把 schema_version 抹掉）。
        ensure_contract_schema(root)
        lock["status"] = "converted"
        if lock.get(SCHEMA_FIELD) is None:
            try:
                lock[SCHEMA_FIELD] = int(read_json(lock_path(root)).get(SCHEMA_FIELD))
            except (ValueError, TypeError):
                lock[SCHEMA_FIELD] = DSH_WRITES_SCHEMA
        write_json(lock_path(root), lock)
        write_migrate_report(root, lock)
    except (OSError, ValueError) as exc:
        _rollback(str(exc))
        return 1
    changed = [item["file"] for item in conversions if item["action"] == "changed"]
    print(f"CONVERTED {len(changed)} file(s) to schema_version={DSH_WRITES_SCHEMA}")
    for item in conversions:
        print(f"- {item['file']}: {item['action']} ({item['detail']})")
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


def window_role_suffix(round_data: Optional[Dict[str, Any]], window: str) -> str:
    """该窗在本轮的职责后缀：verifier 必须与 worker 可区分。"""
    if isinstance(round_data, dict):
        assignments = round_data.get("verifier_assignments")
        if isinstance(assignments, dict):
            if window in {str(value) for value in assignments.values()}:
                return " (verifier)"
    return ""


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
                f"| {window}{window_role_suffix(round_data, window)} | "
                f"{'yes' if window in receipts else 'no'} | "
                f"{window_status.get(window, 'MISSING')} | {task_col} |"
            )
    lines.extend(["", "## 全部任务", ""])
    if not manifests:
        lines.append("- 无 TASK-*")
    else:
        lines.extend(
            [
                "| 任务 | owner | 标题 | 状态 | attempt | 卡点 |",
                "|------|-------|------|------|---------|------|",
            ]
        )
        for task_id, manifest in manifests.items():
            block = manifest.get("block_attempts")
            block_cell = "0"
            if isinstance(block, dict) and block.get("block_id"):
                block_cell = f"{block.get('block_id')} {block.get('attempts', 0)}/{MAX_BLOCK_ATTEMPTS}"
            lines.append(
                f"| {task_id} | {manifest.get('owner', '-')} | "
                f"{manifest.get('title') or '—'} | {manifest.get('status', 'pending')} | "
                f"{manifest.get('attempt', 0)} | {block_cell} |"
            )
    unlisted = [task_id for task_id in manifests if task_id not in listed_tasks]
    if unlisted:
        lines.extend(["", "未列入本轮但存在的任务：" + ", ".join(unlisted)])
    lines.append("")
    return "\n".join(lines) + "\n"


def recorded_gate_state(root: Path, task_id: str) -> str:
    """从 `.task/<task>/rerun.json` 读出**上次真跑 Gate** 的结果。

    `status` 是只读视图：它必须便宜（报告 §4.8：跑一次 status 会因为逐任务
    重跑 ~90s 的回归命令而超时）。因此这里只读已记录的结论，不重新执行任何
    shell 命令。要真跑，用 `gate <task-id>` 或 `audit-round`。
    """
    path = task_dir(root, task_id) / "rerun.json"
    if not path.is_file():
        return "GATE_NOT_RUN"
    try:
        data = read_json(path)
    except ValueError:
        return "GATE_UNKNOWN"
    commands = data.get("commands")
    if not isinstance(commands, list):
        return "GATE_UNKNOWN"
    if data.get("stale_report"):
        return "GATE_STALE"
    return "GATE_PASS" if all(
        isinstance(item, dict) and int(item.get("exit_code", 1)) == 0 for item in commands
    ) else "GATE_FAIL"


def cmd_status(
    root: Path,
    markdown: bool = False,
    write: bool = False,
    deep: bool = False,
) -> int:
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
        if deep:
            gate_state = "GATE_PASS" if not gate(root, task_id, "full") else "GATE_FAIL"
        else:
            gate_state = recorded_gate_state(root, task_id)
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
    # 硬停保护：第三次同根因失败之后，普通换人/转换不得恢复施工（Codex 审阅问题 2）。
    if hard_stop_active(manifest) and hard_stop_blocks(target):
        if not getattr(args, "adjudicate_hard_stop", False) or not str(args.reason or "").strip():
            print("RESULT FAIL")
            print(f"- {hard_stop_refusal(args.task_id, target)}")
            return 1
        manifest["hard_stop_adjudications"] = manifest.get("hard_stop_adjudications", [])
        manifest["hard_stop_adjudications"].append(
            {"at": now_iso(), "by": args.actor, "reason": args.reason}
        )
        record_status(manifest, target, args.actor, f"hard-stop adjudicated: {args.reason}")
        # 裁决后恢复施工 = 新的修复轮：本轮的硬停已结束，再出现同样错误算新失败。
        advance_repair_cycle(manifest)
        write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
        print(f"HARD_STOP_ADJUDICATED {args.task_id} by {args.actor}: {args.reason}")
        print(f"STATUS_CHANGED {args.task_id}: {current} -> {target} by {args.actor}")
        return 0
    record_status(manifest, target, args.actor, reason)
    # 只有"真实发生过施工/交付"的转换才推进修复轮次（Codex rev3 Q3'）：
    # 进入 in_progress = 重新施工；进入 worker_done = 重新交付。
    if work_event_advances_cycle(target):
        advance_repair_cycle(manifest)
    write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
    print(f"STATUS_CHANGED {args.task_id}: {current} -> {target} by {args.actor}")
    return 0


def _reopen_body(
    args: argparse.Namespace, root: Path, *, allow_worker_actor: bool = False
) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Returns (manifest, error, notice)。`notice` 非空表示这是**补充说明**（不算失败）。"""
    # reopen（改状态）只有 M1 能做；attempt（记失败/交接，不改状态）由失败方记录。
    if args.actor != "M1" and not allow_worker_actor:
        return None, "reopened requires actor=M1", ""
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        return None, "\n".join(errors), ""
    owner = str(manifest.get("owner") or "?")
    block, block_error, supplemented = bump_block_attempt(
        manifest,
        block_id=args.block_id,
        reason=args.reason,
        owner=owner,
        new_root=bool(getattr(args, "new_root", False)),
    )
    if block_error and not supplemented:
        return None, block_error, ""
    if supplemented:
        # 同一次失败的补充说明：记证据、不改状态、不加计数。
        manifest["block_attempts"] = block
        write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
        return manifest, "", block_error
    manifest["block_attempts"] = block
    attempts = max(0, int(block.get("attempts", 0) or 0))
    # 第 2 次 fail：必须换真正不同的负责人，任务转为 blocked 并落卡点记录。
    if attempts >= REASSIGN_ON_SAME_BLOCK_FAILURE and attempts < MAX_BLOCK_ATTEMPTS:
        record_status(manifest, "blocked", args.actor, "REASSIGN_REQUIRED")
        manifest["reassign_required"] = True
        manifest["reassign_required_at"] = now_iso()
        write_blocker_note(root, args.task_id, str(block.get("block_id")), attempts, args.reason)
    elif attempts >= MAX_BLOCK_ATTEMPTS:
        # 第 3 次 fail：硬停（与 Codex 版共用同一 HARD_STOP 词汇）。
        record_status(manifest, "blocked", args.actor, "HARD_STOP")
        manifest["hard_stop"] = True
        manifest["hard_stop_at"] = now_iso()
        write_blocker_note(root, args.task_id, str(block.get("block_id")), attempts, args.reason)
    else:
        record_status(manifest, "reopened", args.actor, args.reason)
    # 记下这次失败**留下的**状态：下一条失败靠它判断"期间有没有发生新的事情"。
    last_failure = last_failure_entry(block)
    if last_failure is not None:
        last_failure["status_after"] = str(manifest.get("status") or "")
    manifest["attempt"] = int(manifest.get("attempt", 0) or 0) + 1
    manifest["reopen_reason"] = args.reason
    manifest["reopened_at"] = now_iso()
    write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
    return manifest, "", ""


def cmd_reopen(args: argparse.Namespace, root: Path) -> int:
    manifest, error, notice = _reopen_body(args, root)
    if manifest is None:
        print("RESULT FAIL")
        print(f"- {error}")
        return 1
    block = manifest["block_attempts"]
    print(f"REOPENED {args.task_id}; attempt={manifest['attempt']}")
    print(
        f"BLOCK {block['block_id']} failures={block['attempts']}/{MAX_BLOCK_ATTEMPTS} "
        f"owner={block['history'][-1]['owner']}"
    )
    attempts = block["attempts"]
    if attempts >= MAX_BLOCK_ATTEMPTS:
        print(f"NEXT 已达上限：硬停并写 docs/BLOCKERS/（{NO_ELIGIBLE_REPLACEMENT} 若宿主无合格替换者）")
        print(f"HARD_STOP {args.task_id}; block_id={block['block_id']}; failures={attempts}")
        return 1
    elif attempts >= REASSIGN_ON_SAME_BLOCK_FAILURE:
        print(f"REASSIGN_REQUIRED {args.task_id}; block_id={block['block_id']}; failures={attempts}")
        print(f"NEXT {BLOCK_REPLACEMENT_HINT}")
        return 1
    return 0


def record_handoff(
    manifest: Dict[str, Any],
    *,
    previous: str,
    new_owner: str,
    reason: str,
) -> Tuple[bool, str]:
    """写入一次真实换人；重复的同一交接只确认、不重复写历史。

    方向文件 §6.1：「换人不计失败；重复处理同一次交接不重复增加计数或历史；
    同负责人仅可确认已有交接，不算满足第二次真换人。」

    Returns (recorded, fingerprint)：recorded=False 表示这次是重复提交（已确认）。
    """
    block = manifest.get("block_attempts") if isinstance(manifest.get("block_attempts"), dict) else {}
    block_id = str(block.get("block_id") or "")
    fingerprint = handoff_fingerprint(block_id, previous, new_owner, reason)
    history = manifest.setdefault("assignment_history", [])
    if not isinstance(history, list):
        history = []
        manifest["assignment_history"] = history
    if any(
        isinstance(item, dict) and item.get("handoff_id") == fingerprint for item in history
    ):
        return False, fingerprint
    record = {
        "from": previous or None,
        "to": new_owner,
        "block_id": block_id,
        "failure_count": block.get("attempts"),
        "handoff_id": fingerprint,
        "at": now_iso(),
        "reason": reason,
    }
    history.append(record)
    return True, fingerprint


def handoff_already_recorded(manifest: Dict[str, Any], target: str) -> bool:
    """该窗是否已经作为"接手方"被记过一次真实换人。"""
    history = manifest.get("assignment_history")
    if not isinstance(history, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("to")) == str(target) for item in history
    )


def assigned_windows(manifest: Dict[str, Any]) -> List[str]:
    """已经在交接历史里出现过的窗号（用于把"没有出口"变成"可选出口"）。"""
    history = manifest.get("assignment_history")
    if not isinstance(history, list):
        return []
    seen: List[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        for key in ("from", "to"):
            value = str(item.get(key) or "")
            if value and value not in seen:
                seen.append(value)
    return seen


def cmd_attempt(args: argparse.Namespace, root: Path) -> int:
    """记录同一卡点的一次**新**失败，并可选地交接给新的合格负责人。"""
    role = args.role
    target_actor = "verifier" if role == "verifier" else "worker"
    if args.actor != target_actor:
        print(f"RESULT FAIL\n- role={role} requires actor={target_actor}")
        return 1
    manifest, error, notice = _reopen_body(args, root, allow_worker_actor=True)
    if manifest is None:
        print("RESULT FAIL")
        print(f"- {error}")
        if DUPLICATE_EVENT in error:
            print(
                "NEXT 这是一次重复提交（同一卡点+同一负责人+同一原因）。"
                "换负责人请用 `reassign --owner <窗号>`；确实换了根因请用 `--new-root` 并给证据。"
            )
        return 1
    if notice:
        # 同一次失败的补充说明：已记录证据、未改变状态、未增加计数。
        block = manifest.get("block_attempts") or {}
        print(notice)
        print(
            f"ATTEMPT {args.task_id} block={block.get('block_id')} "
            f"failures={block.get('attempts')}/{MAX_BLOCK_ATTEMPTS} （未增加）"
        )
        return 0
    new_owner = str(args.owner or "").strip()
    if new_owner and new_owner != str(manifest.get("owner") or ""):
        if not valid_window(new_owner):
            print(f"RESULT FAIL\n- --owner must be M1-M10 or Cn, got {new_owner!r}")
            return 1
        previous = str(manifest.get("owner") or "")
        manifest["owner"] = new_owner
        recorded, fingerprint = record_handoff(
            manifest, previous=previous, new_owner=new_owner, reason=args.reason
        )
        # 交接历史独立存一份、不可覆盖：改根因会重置 block 计数，但不得抹掉换人记录。
        block = manifest["block_attempts"]
        history = block.get("history") if isinstance(block.get("history"), list) else []
        history.append(
            {
                "handoff": True,
                "from": previous,
                "to": new_owner,
                "block_id": block.get("block_id"),
                "failure_count": block.get("attempts"),
                "handoff_id": fingerprint,
                "at": now_iso(),
                "reason": args.reason,
            }
        )
        block["history"] = history
        write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
        if recorded:
            print(f"OWNER {previous} -> {new_owner} (block={block.get('block_id')})")
        else:
            print(
                f"{DUPLICATE_EVENT}: 这次交接（{previous} -> {new_owner}）已记录过，"
                "只确认、不重复写历史"
            )
    elif not new_owner:
        print("NOTE no --owner given: this is not a real handoff (same owner keeps the block)")
    block = manifest["block_attempts"]
    print(
        f"ATTEMPT {args.task_id} block={block['block_id']} "
        f"failures={block['attempts']}/{MAX_BLOCK_ATTEMPTS} role={role}"
    )
    if block["attempts"] >= MAX_BLOCK_ATTEMPTS:
        print(f"NEXT 已达上限：硬停并写 docs/BLOCKERS/（必要时 {NO_ELIGIBLE_REPLACEMENT}）")
        # 与 cmd_reopen 统一口径：给出可被脚本识别的 token。
        print(f"{HARD_STOP} {args.task_id}; block_id={block['block_id']}; failures={block['attempts']}")
        return 1
    elif block["attempts"] >= REASSIGN_ON_SAME_BLOCK_FAILURE:
        print(f"REASSIGN_REQUIRED {args.task_id}; block_id={block['block_id']}; failures={block['attempts']}")
        print(f"NEXT {BLOCK_REPLACEMENT_HINT}")
    return 0


def cmd_reassign(args: argparse.Namespace, root: Path) -> int:
    """第 2 次 fail 后换真正不同的负责人（与 Codex 版 `reassign` 语义对齐）。

    只做两件事：把 owner 换成新窗、清掉 `reassign_required`；同时记交接历史。
    它**不是** verifier 指派（那是 `assign-verifier`）。
    """
    if args.actor != "M1":
        print("REASSIGN_FAIL: reassign requires actor=M1")
        return 1
    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        print("REASSIGN_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    new_owner = str(args.owner or "").strip()
    if not valid_window(new_owner):
        print(f"REASSIGN_FAIL: new owner must be M1-M10 or Cn, got {new_owner!r}")
        return 1
    previous = str(manifest.get("owner") or "")
    # 硬停保护：普通换人不得恢复施工（Codex 审阅问题 2，已实测复现）。
    if hard_stop_active(manifest) and not getattr(args, "adjudicate_hard_stop", False):
        print("REASSIGN_FAIL")
        print(f"- {hard_stop_refusal(args.task_id, 'in_progress')}")
        return 1
    if new_owner == previous:
        # 方向文件 §6.1：同负责人**可以确认已有交接**（幂等），但不算满足第二次真换人。
        if handoff_already_recorded(manifest, new_owner):
            print(
                f"HANDOFF_CONFIRMED {new_owner} 已在交接历史中；本次只确认，不新增记录、"
                "不增加失败次数"
            )
            print(
                "NEXT 若本题要求的是**第二次真换人**，请换一个尚未接手过的合格窗："
                + (", ".join(w for w in assigned_windows(manifest) if w != new_owner) or "（暂无）")
            )
            return 0
        print(
            f"REASSIGN_FAIL: new owner must differ from current owner ({previous})；"
            "该窗还没有任何交接记录，无法当作确认。"
        )
        print(
            "可用窗（历史交接过的）："
            + (", ".join(assigned_windows(manifest)) or "（暂无）")
        )
        return 1
    round_data: Optional[Dict[str, Any]] = None
    try:
        round_data = load_round(root)
    except ValueError:
        round_data = None
    if round_data is not None:
        expected = {str(value) for value in round_data.get("expected_windows", [])}
        if expected and new_owner not in expected:
            print(
                f"REASSIGN_FAIL: {new_owner} is not in round.expected_windows; "
                "add/open the window first"
            )
            return 1
    block = manifest.get("block_attempts") if isinstance(manifest.get("block_attempts"), dict) else {}
    manifest["owner"] = new_owner
    recorded, fingerprint = record_handoff(
        manifest, previous=previous, new_owner=new_owner, reason=args.reason
    )
    # 换人不写进 block_history —— 那是一本"失败账"，长度必须等于失败次数；
    # 交接由 assignment_history（不可覆盖）承载。
    if block:
        manifest["block_attempts"] = block
        manifest["block_history"] = (
            block.get("history") if isinstance(block.get("history"), list) else []
        )
    manifest["reassign_required"] = False
    manifest["reassigned_at"] = now_iso()
    adjudicated = hard_stop_active(manifest) and getattr(args, "adjudicate_hard_stop", False)
    record_status(manifest, "in_progress", args.actor, args.reason or "reassigned")
    if adjudicated:
        # 明确裁决解除硬停：留痕并推进修复轮次（此后同样错误算新的失败）。
        manifest["hard_stop_adjudications"] = manifest.get("hard_stop_adjudications", [])
        manifest["hard_stop_adjudications"].append(
            {"at": now_iso(), "by": args.actor, "reason": args.reason, "via": "reassign"}
        )
        manifest["hard_stop"] = False
    advance_repair_cycle(manifest)
    write_json(task_dir(root, args.task_id) / "manifest.json", manifest)
    if round_data is not None:
        tasks = round_data.get("tasks")
        if isinstance(tasks, dict):
            for window, values in tasks.items():
                if isinstance(values, list) and args.task_id in [str(v) for v in values]:
                    tasks[window] = [v for v in values if str(v) != args.task_id]
            tasks.setdefault(new_owner, [])
            if args.task_id not in tasks[new_owner]:
                tasks[new_owner].append(args.task_id)
            round_data["tasks"] = tasks
            write_json(round_path(root), round_data)
    if recorded:
        print(
            f"REASSIGNED {args.task_id}: {previous or '(none)'} -> {new_owner} "
            f"(block={block.get('block_id')})"
        )
    else:
        print(
            f"REASSIGNED {args.task_id}: owner 已是 {new_owner}，"
            f"{previous or '(none)'} -> {new_owner} 这次交接已记录过（只确认，未新增记录）"
        )
    print(
        f"NEXT owner={new_owner}；任务已回到 in_progress。"
        "换人不增加失败次数；同一根因的计数继续沿用。"
    )
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


ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
ANCHOR_FILE_NOT_FOUND = "ANCHOR_FILE_NOT_FOUND"
ANCHOR_LOOSE = "ANCHOR_LOOSE"


def anchor_tokens(anchor: str) -> List[str]:
    """锚点的粗匹配 token。

    **必须跳过 Markdown 前导符号**（`#`、`>`、`-`、`*` 等）再取第一个有意义的词，
    否则 `## 标题` 会拿 `##` 去比对，几乎所有标题都会误判成 LOOSE。
    """
    tokens: List[str] = []
    stripped = anchor.strip()
    if stripped:
        tokens.append(stripped)
    meaningful = stripped.lstrip("#>-*+ \t").strip()
    if meaningful and meaningful not in tokens:
        tokens.append(meaningful)
    words = meaningful.split()
    if words:
        first_word = words[0].strip("`*_：:")
        if first_word and first_word not in tokens:
            tokens.append(first_word)
    return [token for token in dict.fromkeys(tokens) if token]


def check_read_budget_anchors(root: Path, manifest: Dict[str, Any]) -> List[str]:
    """只告警、不阻塞：锚点是否还能在文件里找到。

    故意**不进入 Full Gate、不改任务状态**（Codex 结论：它属于效率设计，
    过早变成监督负担不值得）。仅 `packet --check-anchors` 时输出告警。

    三类结果分开，便于以后统计"锚点漂移率"时不失真：
      ANCHOR_FILE_NOT_FOUND  文件不存在（不是锚点漂移）
      ANCHOR_NOT_FOUND       锚点确实找不到（这才是漂移信号）
      ANCHOR_LOOSE           只是格式变了，粗匹配仍能命中
    """
    warnings: List[str] = []
    raw = manifest.get("read_budget")
    if not isinstance(raw, list):
        return warnings
    for item in raw:
        if not isinstance(item, dict):
            continue
        anchor = str(item.get("anchor") or "").strip()
        raw_path = item.get("path")
        if not anchor or not isinstance(raw_path, str) or not raw_path.strip():
            continue
        target = (root / normalized_path(raw_path)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            warnings.append(f"{ANCHOR_NOT_FOUND} {raw_path}: path escapes project root")
            continue
        if not target.is_file():
            warnings.append(
                f"{ANCHOR_FILE_NOT_FOUND} {raw_path}: 文件不存在（不是锚点漂移，先确认路径）"
            )
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        candidates = anchor_tokens(anchor)
        if candidates and candidates[0] in text:
            continue
        loose = [token for token in candidates[1:] if token in text]
        if loose:
            warnings.append(
                f"{ANCHOR_LOOSE} {raw_path}: 锚点 {anchor!r} 未精确命中，但包含 {loose[0]!r}"
                "（可能只是格式变了，需人工确认）"
            )
        else:
            warnings.append(
                f"{ANCHOR_NOT_FOUND} {raw_path}: 找不到锚点 {anchor!r}；请 M1 重新定位后再派工"
            )
    return warnings


def reading_budget_lines(manifest: Dict[str, Any]) -> List[str]:
    """读取预算：大件只允许按行号区间读。写进简报/就绪包，让工人不必整篇读。

    manifest 里的形状：
      "read_budget": [{"path": "docs/设计表.md", "anchor": "## 升级池", "lines": "60-75"},
                      {"path": "docs/GAME-SPEC.md", "lines": "full"}]
    `anchor` 可选：行号会漂移，锚点让窗口确认自己读的是正确章节（代码可用函数名/类名）。
    """
    raw = manifest.get("read_budget")
    if not isinstance(raw, list) or not raw:
        return [
            "- rule: 任何 >100 行或 >4KB 的文件，只读「行号区间 / 关键词片段」；整篇读取要写明理由",
            "- 本任务未声明 `read_budget`；M1 派工前应补上（把要用的大件与行号区间写清楚）",
        ]
    lines = ["- rule: 下面列出的文件按给定范围读，**其余大件一律不整篇读**"]
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = normalized_path(item.get("path") or "?")
        span = str(item.get("lines") or item.get("range") or "?").strip()
        note = str(item.get("why") or "").strip()
        anchor = str(item.get("anchor") or "").strip()
        suffix = f"（{note}）" if note else ""
        where = f"锚点 {anchor} " if anchor else ""
        if span.lower() == "full":
            lines.append(f"- {path}: **允许整篇**（已声明理由）{suffix}")
        else:
            lines.append(f"- {path}: {where}只读 {span}{suffix}")
    return lines


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


def cmd_brief(
    args: argparse.Namespace, root: Path, out: Optional[Any] = None
) -> int:
    write = out if callable(out) else (lambda text="": print(text))
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
    # 正式派工点：**默认派工路径也走同一套前提校验**（Codex rev2 Q1）——
    # 不是只给 `gate --dispatch` 补检查。校验不通过则不输出正式简报、也不建基线。
    if role == "worker":
        # 首次派工严格校验路由；**已在飞行中**的任务（已有基线、已有失败记录或已推进过
        # attempt）只把路由问题降级为提示——否则恢复期的重新出简报会被旧状态永久挡住。
        in_flight = (
            contract_path(root, args.task_id).is_file()
            or bool(manifest.get("block_attempts"))
            or int(manifest.get("attempt") or 0) > 0
        )
        dispatch_errors = validate_dispatch_ready(
            root, args.task_id, manifest, round_data, strict_routing=not in_flight
        )
        if dispatch_errors:
            print(f"BRIEF_FAIL: {args.task_id} 尚未满足正式派工前提")
            for error in dispatch_errors:
                print(f"- {error}")
            return 1
    owner = str(manifest.get("owner") or "")
    worker_window = task_worker_route(round_data, args.task_id) or owner
    assigned_verifier = task_verifier_route(round_data, args.task_id)
    requested_window = str(getattr(args, "window", "") or "").strip()
    if role == "verifier":
        window = assigned_verifier
        assignment_errors = validate_assignments(
            root,
            round_data if round_data is not None else {},
            args.task_id,
            manifest,
            required=bool(policy["independent_verification"]),
            worker_window=worker_window,
        )
        if not policy["independent_verification"]:
            print(
                "BRIEF_FAIL: this task closes on the short path; a verifier brief must not exist "
                "(低风险短路径不得派 verifier)"
            )
            return 1
        if not assigned_verifier:
            print(
                f"BRIEF_FAIL: {VERIFIER_ASSIGNMENT_MISSING}: no verifier assignment for "
                f"{args.task_id}; run assign-verifier first"
            )
            return 1
        if requested_window and requested_window != assigned_verifier:
            print(
                f"BRIEF_FAIL: {VERIFIER_ASSIGNMENT_CONFLICT}: --window {requested_window} is not "
                f"the assigned verifier ({assigned_verifier})"
            )
            return 1
        if assignment_errors:
            print("BRIEF_FAIL")
            for error in assignment_errors:
                print(f"- {error}")
            return 1
    elif requested_window and requested_window != worker_window:
        print(
            f"BRIEF_FAIL: {WORKER_ASSIGNMENT_CONFLICT}: --window {requested_window} is not the "
            f"worker route ({worker_window})"
        )
        return 1
    else:
        window = worker_window
    allowed = manifest.get("allowed_paths")
    allowed_lines = (
        [f"- {item}" for item in allowed]
        if isinstance(allowed, list) and allowed
        else ["- (empty — fill before dispatch)"]
    )
    write(f"BRIEF {args.task_id} role={role}")
    write(f"# 任务简报 — {args.task_id} / {role}")
    write()
    write(f"- 窗号: {window or '(unassigned)'}")
    if role == "verifier":
        write("- 当前职责: verifier / 独立验收（不接管实现）")
        write(f"- 实现 owner: {owner or '(none)'}")
        write(f"- 实现窗号: {worker_window or '(none)'}")
    else:
        write(f"- 当前职责: {role}")
    write(f"- 标题: {manifest.get('title') or '(none)'}")
    write(f"- owner: {owner or '(none)'}")
    write(f"- 任务状态: {manifest.get('status', 'pending')}")
    write(f"- risk: {policy['risk']}")
    write(f"- attempt: {policy['attempt']}")
    write(f"- 卡点: {block_budget_line(manifest)}")
    write(f"- 收口路径: {close_path_label(policy)}")
    if policy.get("conflict"):
        write(f"- POLICY_CONFLICT: {policy['conflict']}")
    # 验收者专用：把**当前标准变化**及其指纹直接给出，验收 agent 照抄进报告即可。
    # 这样"接受标准变化"不再需要用户或 M1 手工查字段（Codex rev3 Q2'）。
    if role == "verifier":
        diffs, _ = contract_diffs(root, args.task_id, manifest)
        if diffs:
            write("")
            write("## 标准变化审查（本任务是变更后的标准）")
            write(f"- 变化指纹 contract_key: `{acceptance_key(diffs)}`")
            write(f"- 与原标准的差异: {describe_diffs(diffs)}")
            write(
                "- 请在 `verify-report.json` 里写："
                f"`\"contract_key\": \"{acceptance_key(diffs)}\"` 与 "
                "`\"standard_verdict\": \"equivalent\"|\"stricter\"|\"accepted\"`"
            )
            write(
                "- `standard_verdict` 是**针对本次变化**的独立结论；"
                "功能验收的 `result` 不能代替它。未写这两项时 M1 无法接受该变化。"
            )
    write()
    write("## allowed_paths")
    write("\n".join(allowed_lines))
    write()
    write("## source_refs（来源 → R）")
    refs = manifest.get("source_refs")
    if not isinstance(refs, list) or not refs:
        write("- (missing — 用户需求必须先写入 source_refs 再派工)")
    else:
        for item in refs:
            if not isinstance(item, dict):
                continue
            mapped = item.get("maps_to")
            mapped_text = ", ".join(str(value) for value in mapped) if isinstance(mapped, list) else "(none)"
            write(f"- {item.get('id')}: {item.get('text') or ''} → {mapped_text}")
    write()
    write("## 读取预算（先看这里，再读任何大件）")
    write("\n".join(reading_budget_lines(manifest)))
    write()
    write("## R 项与验收")
    write("\n".join(format_requirements(manifest)))
    write()
    write("## 硬规则")
    write("\n".join(role_hard_rules(role, policy)))
    write()
    write("## 报告格式")
    write("\n".join(role_report_format(role)))
    write()
    write("## 开工句")
    write(f"我是 {window or '{窗号}'} 窗口。只按本简报执行。不要打开网页预览。")
    return 0


def cmd_packet(args: argparse.Namespace, root: Path) -> int:
    """就绪包：窗口读这一份即可开工，不必再翻 Registry 或整篇设计表。

    = brief 的全部内容 + 读取预算（大件只给行号区间）+ 输出预算。
    """
    lines: List[str] = []

    def sink(text: str = "") -> None:
        lines.append(text)

    code = cmd_brief(args, root, sink)
    if code != 0:
        for line in lines:
            print(line)
        return code

    manifest, errors = load_manifest(root, args.task_id)
    if manifest is None:
        for line in lines:
            print(line)
        print("PACKET_FAIL: missing task")
        for error in errors:
            print(f"- {error}")
        return 1
    round_data: Optional[Dict[str, Any]] = None
    try:
        round_data = load_round(root)
    except ValueError:
        round_data = None
    role = str(args.role or "").strip().lower()
    owner = str(manifest.get("owner") or "")
    worker_window = task_worker_route(round_data, args.task_id) or owner
    window = (
        task_verifier_route(round_data, args.task_id)
        if role == "verifier"
        else worker_window
    )
    header = [
        f"# 就绪包 — {args.task_id} / {role} / {window or '(unassigned)'}",
        "",
        "> 你只需要读这一份。**不要去读整篇设计表/规格，除非下面的「读取预算」允许整篇。**",
        "> 大件按行号区间读；读进来的内容之后每一步都会被重发，所以读得越少越快。",
        "> **本包是可再生成的派工副本，禁止手改。** 状态与约束一律以 `.task/` 的 manifest / round 为准。",
        "> 读取预算是**派工约束**（减少无效上下文），不是可完全证明的安全权限边界。",
        "",
    ]
    footer = [
        "",
        "## 输出预算（硬性）",
        "- 成功回执 10～20 行；失败/阻塞可超限，但必须说明原因",
        "- 格式：状态 + 路径 + 关键行号/片段 + 验收命令 + **命令输出原文** + 未决项",
        "- **不要复述本包内容**，不要重述设计表段落；命令输出原文必须完整保留（它是证据）",
        "",
        "## 归属确认",
        f"- 你是 {window or '{窗号}'}；本任务实现 owner 是 {owner or '(none)'}",
        f"- 收口路径：{close_path_label(verification_policy(manifest))}；最终收口只由 M1 做",
    ]
    # 验收命令强度：**仅辅助提示**，不判定、不影响收口（方向文件 §6.7）。
    # manifest 的 verify_cmd 与 worker-report.tests[] 都要看——工人写的命令在那一边。
    weak_report: Optional[Dict[str, Any]] = None
    report_candidate = task_dir(root, args.task_id) / "worker-report.json"
    if report_candidate.is_file():
        try:
            weak_report = read_json(report_candidate)
        except ValueError:
            weak_report = None
    weak = weak_acceptance_notes(collect_verify_commands(manifest, weak_report))
    if weak:
        footer.extend(
            [
                "",
                "## 验收命令提示（仅供参考，不是判定）",
                "- 下列命令形态偏弱；是否足够由验收者按**需求与回归风险**判断，工具不代替判断：",
            ]
            + [f"- {note}" for note in weak]
        )
    text = "\n".join(header + lines + footer) + "\n"
    if getattr(args, "check_anchors", False):
        for warning in check_read_budget_anchors(root, manifest):
            print(f"WARN {warning}")
    if getattr(args, "write", False):
        target = task_dir(root, args.task_id) / f"packet-{window or role}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"PACKET_WRITTEN {target.relative_to(root)}")
    print(text, end="")
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
                print(f"- WINDOW {window}{window_role_suffix(round_data, window)}: {window_status.get(window, 'MISSING')}")
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
        description="multi-window-m-036 v0.36 task gate",
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
    round_init.add_argument(
        "--verifier",
        action="append",
        default=[],
        help="independent verification route, e.g. --verifier TASK-001=C2",
    )

    assign_verifier = sub.add_parser(
        "assign-verifier",
        help="assign the independent verifier window (does not change owner/attempt/block)",
    )
    assign_verifier.add_argument("task_id")
    assign_verifier.add_argument("--window", required=True)
    assign_verifier.add_argument("--actor", choices=["M1"], default="M1")

    sync_route = sub.add_parser(
        "sync-worker-route",
        help="restore a mis-routed task in round.tasks back to manifest.owner",
    )
    sync_route.add_argument("task_id")
    sync_route.add_argument("--actor", choices=["M1"], default="M1")

    receipt = sub.add_parser("receipt", help="record a window receipt")
    receipt.add_argument("window")
    receipt.add_argument(
        "--role",
        choices=["worker", "verifier"],
        default="worker",
        help="verifier receipts must come from an assigned verifier",
    )

    sub.add_parser("request-check", help="request the round audit")

    gate_parser = sub.add_parser("gate", help="run a task gate")
    gate_parser.add_argument("task_id")
    gate_parser.add_argument(
        "--basic",
        action="store_true",
        help="basic acceptance: check worker material only; do not re-run commands",
    )
    gate_parser.add_argument(
        "--dispatch",
        action="store_true",
        help="dispatch point: require ready requirements and (re)build the contract baseline",
    )

    sub.add_parser("audit-round", help="audit the current round if check_requested")
    sub.add_parser("audit", help="alias for audit-round")
    hook = sub.add_parser("hook-audit", help="record a host stop-hook run and audit the current round")
    hook.add_argument(
        "--source",
        choices=[
            "manual",
            "codex-stop",
            "cursor-stop",
            "zcode-stop",
            "dsh-stop",
            "dsh-subagent-end",
            "dsh-session-start",
            "dsh-prompt",
            "dsh-pre-tool",
            "dsh-post-tool",
        ],
        default="manual",
    )
    hook.add_argument("--host", choices=["cursor", "codex", "zcode", "dsh", "manual"])
    hook.add_argument(
        "--session",
        default="",
        help="host session id; becomes run_id so one window's dsh events pair up",
    )
    hook.add_argument("--agent", default="", help="dsh subagent id (SubagentStop payload)")
    hook.add_argument(
        "--intent",
        choices=["audit", "pair", "record"],
        default="audit",
        help="dsh only: pair writes a complete start/end pair in one call (default "
        "for dsh sources), record writes a single observation entry",
    )
    migrate = sub.add_parser("migrate-project", help="backup, report, copy this v0.36 taskctl, then --check")
    migrate.add_argument("--destination", default="scripts/taskctl.py")
    migrate.add_argument("--force", action="store_true", help="replace an existing destination after backup")
    migrate.add_argument(
        "--check",
        action="store_true",
        help="run basic/full/hook/negative smokes; require MIGRATE_READY before new work",
    )
    status_parser = sub.add_parser(
        "status",
        help="show window and task states (read-only; never executes shell commands)",
    )
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
    status_parser.add_argument(
        "--deep",
        action="store_true",
        help="explicitly re-run the Full Gate for every task; this executes regression "
        "commands and can take minutes, so it is off by default",
    )

    round_close = sub.add_parser(
        "round-close",
        help="archive round.json to .task/rounds/ (only when every task is done and the audit passes)",
    )
    round_close.add_argument("--actor", choices=["M1"], default="M1")

    transition = sub.add_parser("transition", help="change a task status through the controlled state machine")
    transition.add_argument("task_id")
    transition.add_argument("status", choices=sorted(TASK_STATES))
    transition.add_argument("--actor", choices=["M1", "worker", "verifier", "manual"], required=True)
    transition.add_argument("--reason")
    transition.add_argument(
        "--adjudicate-hard-stop",
        action="store_true",
        help="explicitly adjudicate an active hard stop (requires --reason with evidence)",
    )

    reopen = sub.add_parser("reopen", help="reopen a task, count the block failure, increment attempt")
    reopen.add_argument("task_id")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--actor", choices=["M1"], default="M1")
    reopen.add_argument("--block-id", required=True, help="current root-cause id; renaming is not a new root cause")
    reopen.add_argument("--new-root", action="store_true", help="declare a genuinely new root cause (needs evidence in --reason)")

    attempt = sub.add_parser(
        "attempt",
        help="record one failure of the current block and the handoff to a real new owner",
    )
    attempt.add_argument("task_id")
    attempt.add_argument("--block-id", required=True, help="current root-cause id")
    attempt.add_argument("--reason", default="", help="failure evidence; required with --new-root")
    attempt.add_argument("--actor", choices=["worker", "verifier"], required=True)
    attempt.add_argument("--role", choices=["worker", "verifier"], default="worker")
    attempt.add_argument("--owner", help="new owning window; omit to record the failure without a handoff")
    attempt.add_argument("--new-root", action="store_true", help="declare a genuinely new root cause (needs evidence in --reason)")

    reassign = sub.add_parser(
        "reassign",
        help="level-2 step: hand the block to a genuinely different owner window",
    )
    reassign.add_argument("task_id")
    reassign.add_argument("--owner", required=True, help="new owning window (must differ from the current one)")
    reassign.add_argument("--reason", default="", help="why this owner can do better")
    reassign.add_argument(
        "--adjudicate-hard-stop",
        action="store_true",
        help="explicitly adjudicate an active hard stop; requires --reason with evidence",
    )
    reassign.add_argument("--actor", choices=["M1"], default="M1")
    brief = sub.add_parser("brief", help="print a copy-ready task brief for a role")
    brief.add_argument("task_id")
    brief.add_argument("--role", required=True, help="worker, scout, or verifier")
    brief.add_argument(
        "--window",
        help="expected window; a verifier brief requires the assigned verifier window",
    )

    packet = sub.add_parser(
        "packet",
        help="print a self-contained ready packet (brief + read budget + output budget)",
    )
    packet.add_argument("task_id")
    packet.add_argument("--role", required=True, help="worker, scout, or verifier")
    packet.add_argument("--window", help="expected window; verifier needs the assigned one")
    packet.add_argument(
        "--write",
        action="store_true",
        help="also write .task/<task>/packet-<window>.md",
    )
    packet.add_argument(
        "--check-anchors",
        action="store_true",
        help="warn (never block) when an anchor can no longer be found in its file",
    )
    sub.add_parser("handoff", help="print an M1 succession brief from .task/")
    adjudicate = sub.add_parser(
        "adjudicate",
        help="M1 records an explicit adjudication (currently: accept an acceptance-standard change)",
    )
    adjudicate.add_argument("task_id")
    adjudicate.add_argument("kind", choices=["accept"], help="accept = accept the current standard change")
    adjudicate.add_argument("--reason", required=True, help="evidence for accepting the change")
    adjudicate.add_argument(
        "--origin",
        default="unspecified",
        help="where the change came from (e.g. 'user added a new requirement'); recorded for audit",
    )
    adjudicate.add_argument("--actor", choices=["M1"], default="M1")
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
    # fail closed：`.task/` 声明了比本宿主更新的 schema 时，禁止任何写操作。
    # 只读子命令（status / gate / audit-round / brief / packet / handoff / selftest）
    # 仍然可用，便于用户查看"更新版宿主写出来的状态"。
    if is_mutating_command(tokens):
        blocked = guard_mutation(root)
        if blocked:
            return blocked
        regressed = guard_schema_regression(root)
        if regressed:
            return regressed
    try:
        if args.command == "init":
            return cmd_init(args, root)
        if args.command == "round-init":
            return cmd_round_init(args, root)
        if args.command == "round-close":
            return cmd_round_close(args, root)
        if args.command == "receipt":
            return cmd_receipt(args, root)
        if args.command == "request-check":
            return cmd_request_check(root)
        if args.command == "assign-verifier":
            return cmd_assign_verifier(args, root)
        if args.command == "sync-worker-route":
            return cmd_sync_worker_route(args, root)
        if args.command == "gate":
            return cmd_gate(args, root)
        if args.command in {"audit-round", "audit"}:
            return cmd_audit_round(root)
        if args.command == "hook-audit":
            return cmd_hook_audit(
                root,
                args.source,
                getattr(args, "host", None),
                getattr(args, "session", "") or "",
                getattr(args, "agent", "") or "",
                getattr(args, "intent", "audit") or "audit",
            )
        if args.command == "migrate-project":
            return cmd_migrate_project(args, root)
        if args.command == "status":
            return cmd_status(
                root,
                markdown=bool(getattr(args, "markdown", False)),
                write=bool(getattr(args, "write", False)),
                deep=bool(getattr(args, "deep", False)),
            )
        if args.command == "transition":
            return cmd_transition(args, root)
        if args.command == "reopen":
            return cmd_reopen(args, root)
        if args.command == "reassign":
            return cmd_reassign(args, root)
        if args.command == "attempt":
            return cmd_attempt(args, root)
        if args.command == "brief":
            return cmd_brief(args, root)
        if args.command == "packet":
            return cmd_packet(args, root)
        if args.command == "handoff":
            return cmd_handoff(root)
        if args.command == "adjudicate":
            return cmd_adjudicate(args, root)
        if args.command == "selftest":
            return cmd_selftest()
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
