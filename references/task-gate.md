# 任务门禁细节

当前行为摘要在 `SKILL.md`「任务控制」。本文件是 schema、策略表、命令与 G0～G3 的唯一详细说明。历史版本见 `CHANGELOG.md`。

## 两轴分离

| 轴 | 存储 | 谁改 | 含义 |
|----|------|------|------|
| 任务状态 | `.task/TASK-xxx/manifest.json` 的 `status` | 仅 `taskctl.py transition` / `reopen` | 任务是否走完合法路径 |
| 窗口状态 | `.task/round.json` 的 `window_status` | 派工与 receipt 流程 | 该窗是否已交查收信号 |

不要写 `M2 verified` 当任务完成。应写 `TASK-001: verified`、`M2: worker_done`。`taskctl status` 可在窗口行旁标注任务终态（全部 `done` 时 `note=tasks_closed`），**不改写** `window_status`。

Registry / RECEIPT-LOG 是给人看的文档层，不能代替 manifest 状态。

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
- `reopened`：`M1`，同时递增 `attempt`

`verified`、`integrated`、`done` 迁移前脚本会重新运行 Full Gate；`done` 还必须从 `integrated` 进入。人工可改为 `paused`、`blocked` 或 `reopened`，但不能无证据伪造 `verified` / `done`。

## 门禁分层

- **G0**：任务目录、manifest、工人报告、独立验收报告是否存在（后者仅策略要求独立验收时强制）。
- **G1**：R 编号是否完整覆盖，是否有对应证据。
- **G2**：测试退出码、diff 越界；只允许改 `allowed_paths`。Full Gate 会真跑命令、核 evidence 路径、有 Git 时核工作区。`manifest.json` / `rerun.json` / `verify-report.json` 未列入工人 `changed_files` 不算漏报。`src/`、`tests/`、`worker-report.json`、evidence 仍必须申报。路径只去掉 `./` 前缀，保留 `.task/`。
- **G3**：策略要求独立验收时，必须有独立验收报告，且 `reviewer` 不得等于实现窗口。

`--basic` 只跑到工人材料，不重跑命令。Hook 不标 done，也不因重跑失败去改状态。

## `audit-round` 输出

- `BASIC_GATE_PASS` + `VERIFY_REQUIRED`：基础材料齐全，但仍需独立验收。
- `BASIC_GATE_PASS` + `FULL_GATE_FAIL`：有任务未通过，不能收口。
- `BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE`：本轮所有任务均通过，M1 才能逐项 `done`。
- `POLICY_CONFLICT`：策略无法唯一决定，停止收口。

脚本负责输出 `RESULT PASS` / `RESULT FAIL` / `POLICY_CONFLICT`；agent 不得只凭口头或自行打印 PASS。

## 任务目录

```text
.task/
├─ round.json
├─ hook-runs.jsonl          # gitignore；最多 100 行
├─ TASK-001/
│  ├─ manifest.json
│  ├─ worker-report.json
│  ├─ verify-report.json    # 仅独立验收路径
│  ├─ rerun.json            # Full Gate 写入，不要手改当证据
│  └─ evidence/
└─ （项目内可选 scripts/taskctl.py 副本）
```

## manifest.json

最小字段：`task_id`、`owner`、`track`、`risk`、`allowed_paths`、`requirements`、`attempt`。每个 requirement 至少包含 `id`、`text`、`verify`。可跑的验收命令写在 `verify_cmd`，或把 `verify` 写成可直接执行的命令。

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
py -3 scripts/taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
py -3 scripts/taskctl.py transition TASK-001 in_progress --actor M1
py -3 scripts/taskctl.py transition TASK-001 worker_done --actor worker
py -3 scripts/taskctl.py transition TASK-001 integrated --actor M1
py -3 scripts/taskctl.py transition TASK-001 done --actor M1
py -3 scripts/taskctl.py transition TASK-001 verifying --actor verifier
py -3 scripts/taskctl.py transition TASK-001 verified --actor verifier
```

`brief` / `handoff` 只打印，不改 `.task/` 状态。`--role` 仅 `worker|scout|verifier`。缺失 task 或非法 role → `BRIEF_FAIL`。无 `.task/` → `HANDOFF_FAIL`。

前四条 `transition` 是 **low 且 attempt < 2** 的短路径（M1 查收时本窗重跑 Full Gate 后再 `integrated`）。后两条仅在策略表要求独立验收时使用；medium/high 或 `attempt >= 2` **禁止** `worker_done → integrated`。

无 Git 且从子目录启动时：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> status
```

`--root` 写在子命令后面会被拒绝。脚本不会盲目继承父目录的 `.task/`。
