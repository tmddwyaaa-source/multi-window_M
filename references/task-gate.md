# 任务门禁细节

当前行为摘要在 `SKILL.md`「任务控制」。本文件是 schema、策略表、命令与 G0～G3 的唯一详细说明。历史版本见 `CHANGELOG.md`。

## 两轴分离

| 轴 | 存储 | 谁改 | 含义 |
|----|------|------|------|
| 任务状态 | `.task/TASK-xxx/manifest.json` 的 `status` | 仅 `taskctl.py transition` / `reopen` | 任务是否走完合法路径 |
| 窗口状态 | `.task/round.json` 的 `window_status` | 派工与 receipt 流程 | 该窗是否已交查收信号 |

不要写 `M2 verified` 当任务完成。应写 `TASK-001: verified`、`M2: worker_done`。`taskctl status` 可在窗口行旁标注任务终态（全部 `done` 时 `note=tasks_closed`），**不改写** `window_status`。

Registry / RECEIPT-LOG 是给人看的路径与叙事，**不能代替** manifest 状态。启用 `.task/` 后，状态表只允许来自：

```text
py -3 scripts/taskctl.py --root <项目根> status --markdown --write
```

生成 `docs/TASK-STATUS.md`（文件头有 `taskctl:generated-status`）。禁止手改。手写 Registry/RECEIPT 的 pending/done 与本表冲突时，以本表和 `.task/` 为准。

## 唯一策略表

实现：`verification_policy()`。Gate、`audit-round`、`transition`、M1 收口必须调用它，禁止各写一套。

| 条件 | 独立验收 | 唯一收口路径 |
|------|----------|--------------|
| **low** 且 `attempt < 2` 且未设 `verification_required` | 否 | `worker_done` → `integrated`（`--actor M1`）→ `done` |
| **medium/high**，或 `attempt >= 2`，或 `verification_required` | 是 | `worker_done` → `verifying` → `verified` → `integrated` → `done` |

`independent_verification` 与 `allow_worker_done_to_integrated` 必须互斥。无法唯一决定，或 manifest 里显式 `independent_verification` 与 `risk` / `attempt` / `verification_required` 矛盾，或 `risk` 不是 `low|medium|high` → 输出 `POLICY_CONFLICT` 并停止。

角色约束：

- `in_progress`：`M1`
- `worker_done`：`worker`
- `verifying`、`verified`：`verifier`
- `integrated`、`done`：`M1`（短路径上 `worker_done → integrated` 也必须是 M1）
- `reopened`：`M1`，同时递增 `attempt`（任务级）并计入当前 `block_attempts`（卡点级）

`verified`、`integrated`、`done` 迁移前脚本会重新运行 Full Gate；`done` 还必须从 `integrated` 进入。人工可改为 `paused`、`blocked` 或 `reopened`，但不能无证据伪造 `verified` / `done`。

## 门禁分层

- **G0**：任务目录、manifest、工人报告、独立验收报告是否存在（后者仅策略要求独立验收时强制）。
- **G1**：R 编号是否完整覆盖，是否有对应证据。
- **G2**：测试退出码、diff 越界；只允许改 `allowed_paths`。Full Gate 会真跑命令、核 evidence 路径、有 Git 时核工作区。`manifest.json` / `rerun.json` / `verify-report.json` 未列入工人 `changed_files` 不算漏报。`src/`、`tests/`、`worker_report.json`、evidence 仍必须申报。路径只去掉 `./` 前缀，保留 `.task/`。

> **工作区核对的前提（实测确认）：** 只有 `.git` 存在**且已有 HEAD**（至少一次提交）时，`git diff HEAD` 才会成功，未申报变更才会被拦。
> 刚 `git init` 但还没首次提交的仓库会让 `git_changed_files()` 返回 `skipped`，**静默跳过**工作区核对——此时改了 `allowed_paths` 内未申报的文件也不会报错。
> 所以：靠 G2 卡"未申报改动"的项目，**开工前必须先有一次提交**。无 Git 或未提交时，改为靠 `changed_files` 申报纪律与本窗复跑，不要以为 G2 在替你兜底。
- **G3**：策略要求独立验收时，必须有独立验收报告，且 `reviewer` 不得等于实现窗口。

`--basic` 只跑到工人材料，不重跑命令。Hook 不标 done，也不因重跑失败去改状态。

## `audit-round` 输出

- `BASIC_GATE_PASS` + `VERIFY_REQUIRED`：基础材料齐全，但仍需独立验收。
- `BASIC_GATE_PASS` + `FULL_GATE_FAIL`：有任务未通过，不能收口。
- `BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE`：本轮所有任务均通过，M1 才能逐项 `done`。
- `POLICY_CONFLICT`：策略无法唯一决定，停止收口。
- `VERIFIER_ASSIGNMENT_MISSING`：需要独立验收但没有 `round.verifier_assignments[T]`。
- `VERIFIER_ASSIGNMENT_CONFLICT`：verifier 不在本轮窗内、等于 owner/worker、短路径却派了 verifier，或 `verify-report.reviewer` 不等于被指派的 verifier（冒名验收）。
- `WORKER_ASSIGNMENT_CONFLICT`：`round.tasks` 里该任务的 worker 窗不等于 `manifest.owner`。
- `REQUIREMENT_COVERAGE_FAIL`：有用户需求未映射到 R 项，停止收口。
- `GEAR_VIOLATION`：未确认却宣称 bonus / 协作挡 A，停止收口。
- `HOOK_EVIDENCE_MISSING`：声明了 hook 监督但没有宿主 stop 的 start/end 对，停止收口。
- `PARALLEL_FAIL`：子代理同文件并发、重复、工人兼 verifier 或越权路径，停止收口。
- `MIGRATE_FAIL`：无备份许可的覆盖、无 lock 就 `--check`、或把运行中的脚本迁到自己身上。
- `MIGRATE_READY` / `MIGRATE_CHECK_FAIL`：`migrate-project --check` 的四项迁移健康检查。

脚本负责输出 `RESULT PASS` / `RESULT FAIL` / `POLICY_CONFLICT`；agent 不得只凭口头或自行打印 PASS。

## 任务目录

```text
.task/
├─ round.json
├─ hook-runs.jsonl          # gitignore；Cursor/Codex/Zcode 证据；最多 100 行
├─ dsh-runs.jsonl           # gitignore；dsh 证据账本；最多 400 行
├─ dsh-hook-status.json     # 可选，仅当 dsh 桥接用了 --status；gitignore
├─ TASK-001/
│  ├─ manifest.json
│  ├─ worker-report.json
│  ├─ verify-report.json    # 仅独立验收路径
│  ├─ rerun.json            # Full Gate 写入，不要手改当证据
│  └─ evidence/
└─ （项目内可选 scripts/taskctl.py 副本）
```

## manifest.json

最小字段：`task_id`、`owner`、`track`、`risk`、`allowed_paths`、`source_refs`、`requirements`、`attempt`。每个 requirement 至少包含 `id`、`text`、`verify`。可跑的验收命令写在 `verify_cmd`，或把 `verify` 写成可直接执行的命令。

**两个计数轴，不要混用：**

| 字段 | 含义 | 作用 | 重置规则 |
|---|---|---|---|
| `attempt` | 任务级历史失败次数 | **只驱动验收策略**（`attempt >= 2` → 独立验收） | 单调递增，不重置 |
| `block_attempts` | **当前卡点**的失败次数 | **只驱动三级阶梯**（1 复用 / 2 换人 / 3 硬停） | 新根因（`--new-root` + 证据）才重置 |

`block_attempts` 结构：

```json
{
  "block_id": "B1",
  "attempts": 2,
  "history": [
    {"attempt": 1, "at": "…", "owner": "M2", "reason": "缺 R1 证据"},
    {"attempt": 2, "at": "…", "owner": "C1", "reason": "同因仍失败"}
  ]
}
```

`assignment_history`（**独立存放、不可覆盖**；换人时追加，改根因也不得抹掉）：

```json
[{"from": "M2", "to": "C1", "block_id": "B1", "failure_count": 2, "at": "…", "reason": "…"}]
```

**三级阶梯（跨宿主稳定性规则）**：

```text
第 1 次 fail → 复用原执行者（先让它复述打回项）
第 2 次 fail → 换真正不同的负责窗口/独立执行上下文，并更新 owner
第 3 次 fail → 硬停；写 docs/BLOCKERS/
宿主无合格替换者 → 写 BLOCKERS NO_ELIGIBLE_REPLACEMENT_WORKER，不得伪造换人
```

命令：

```text
py -3 scripts/taskctl.py --root <项目根> attempt TASK-001 --block-id B1 --reason "<证据>" --actor worker
py -3 scripts/taskctl.py --root <项目根> attempt TASK-001 --block-id B1 --reason "<打回项>" --actor worker --owner C1
py -3 scripts/taskctl.py --root <项目根> attempt TASK-001 --block-id B2 --new-root --reason "<新根因证据>" --actor worker
```

拒绝规则（都有负向用例）：同一卡点第 4 次 → 拒；改 `block_id` 不给 `--new-root` → 拒；`--new-root` 不给证据 → 拒；`--role` 与 `--actor` 不一致 → 拒。

`source_refs` 每条：`id`、`text`（用户原始需求）、`maps_to`（非空 R 编号列表）。每条 R 必须被映射到。`round.json` 可选 `source_requirements`；列出的 id 必须出现在某任务的 `source_refs` 里。未映射 → `REQUIREMENT_COVERAGE_FAIL`。禁止只改聊天话术加需求。

`manifest.read_budget`（可选，但派工前**应当**补）：告诉窗口哪些大件按什么范围读。

```json
"read_budget": [
  {"path": "docs/设计表.md", "anchor": "## 2.4 升级池", "lines": "68-105", "why": "只改池"},
  {"path": "docs/GAME-SPEC.md", "lines": "full", "why": "短文件"}
]
```

- `lines` 只接受 `N-M` 或 `full`；其它形状 → Full Gate 拒绝（`read_budget[i] lines must be 'N-M' or 'full'`）。
- `anchor` 可选（给了就不能为空）：行号会漂移，锚点用于确认读到的是**正确章节**；代码文件可用函数名/类名。
- 渲染进 `brief` / `packet` 的「读取预算」段。
- 语义：**只有这里列出的文件允许按给定范围读；其余大件一律不整篇读**。`full` 表示已声明理由允许整篇。
- **定位由 M1 做**：M1 负责确保范围已定位并写入，可由斥候辅助；**不得要求用户提供行号**。
- **它是派工约束，不是安全权限边界**——只用于减少无效上下文，不提供隔离保证。

`round.json` 可选：

- `tasks`：**实现路由**。`{"C1": ["TASK-001"]}` —— 该任务的 worker 窗必须等于 `manifest.owner`，否则 `WORKER_ASSIGNMENT_CONFLICT`。
- `verifier_assignments`：**验收路由**（0.34）。`{"TASK-001": "C2"}`。需要独立验收的任务必须有；verifier 必须在 `expected_windows` 内且不同于 owner/worker；短路径任务不得有。`assign-verifier` 只改这里，不改 `owner` / `attempt` / `block_id` / `assignment_history`。
- `receipts_by_role`：由 `receipt --role` 写入，`{"C2": "verifier"}`，让状态表能把 C2 显示为 verifier 而不是"又一个 worker"。
- `gears`：`capability`=`default|bonus`，`collaboration`=`P|A`，`capability_confirmed` 布尔。未写 = 默认 + P。bonus 或 A 必须 `capability_confirmed=true`，否则 `GEAR_VIOLATION`。
- `hook_supervision`：默认 false。为 true 时，`audit-round` 收口需要宿主 stop 的完整 start/end 对：Cursor / Codex / Zcode 看 `hook-runs.jsonl` 里来自 `cursor-stop` / `codex-stop` / `zcode-stop` 的对；dsh 看 `dsh-runs.jsonl` 里来自 `dsh-stop` / `dsh-subagent-end` 的对（接线见 [hooks.md](hooks.md)、[dsh-evidence.md](dsh-evidence.md)）。`--source manual` 不算。缺证据 → `HOOK_EVIDENCE_MISSING`。宿主没有 hook 能力时才不要打开此开关。
- `subagents`：每条 `run_id`、`task_id`、`window`、`role`（`worker|scout|verifier`）、`allowed_paths`。路径必须落在该任务 `allowed_paths` 内。同文件并发、重复 `run_id`、同一窗既 worker 又 verifier → `PARALLEL_FAIL`。**子代理身份不等于正式窗号**：它只是某个 C/M 窗的执行载体。

不要整份覆盖 `manifest.json` 来改状态。不要把显式 `independent_verification` 写成与 `risk` / `attempt` / `verification_required` 相反的值。

## 常用命令

`--root` 必须在子命令前面。

```text
py -3 scripts/taskctl.py selftest
py -3 scripts/taskctl.py brief TASK-001 --role worker
py -3 scripts/taskctl.py handoff
py -3 scripts/taskctl.py init TASK-001
py -3 scripts/taskctl.py gate TASK-001
py -3 scripts/taskctl.py audit-round
py -3 scripts/taskctl.py reopen TASK-001 --reason "人工验收发现遗漏"
py -3 scripts/taskctl.py status
py -3 scripts/taskctl.py status --markdown
py -3 scripts/taskctl.py status --markdown --write
py -3 scripts/taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
py -3 scripts/taskctl.py --root <项目根> migrate-project --check
py -3 scripts/taskctl.py transition TASK-001 in_progress --actor M1
py -3 scripts/taskctl.py transition TASK-001 worker_done --actor worker
py -3 scripts/taskctl.py transition TASK-001 integrated --actor M1
py -3 scripts/taskctl.py transition TASK-001 done --actor M1
py -3 scripts/taskctl.py transition TASK-001 verifying --actor verifier
py -3 scripts/taskctl.py transition TASK-001 verified --actor verifier
```

`brief` / `handoff` / `status --markdown` 默认不改 `.task/` 状态。`--write` 只覆盖 `docs/TASK-STATUS.md`。无 `.task/` 时 `--markdown` → `STATUS_FAIL: no .task`。`--role` 仅 `worker|scout|verifier`。缺失 task 或非法 role → `BRIEF_FAIL`。无 `.task/` 跑 `handoff` → `HANDOFF_FAIL`。

`migrate-project` 覆盖已有 `scripts/taskctl.py` 必须 `--force`，且先备份。报告与 lock 写完后再 `--check`。未 `MIGRATE_READY` 不要开发新功能。`--check` 不要求项目内所有任务 Full Gate PASS，只要求迁入的脚本能跑门禁、Hook 不改状态、负向路径仍被拒绝。

前四条 `transition` 是 **low 且 attempt < 2** 的短路径（M1 查收时本窗重跑 Full Gate 后再 `integrated`）。后两条仅在策略表要求独立验收时使用；medium/high 或 `attempt >= 2` **禁止** `worker_done → integrated`。

无 Git 且从子目录启动时：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> status
```

`--root` 写在子命令后面会被拒绝。脚本不会盲目继承父目录的 `.task/`。
