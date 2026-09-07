# multi-window_M 历史版本

当前规则以 `SKILL.md` 的 `version` 字段为准。本文件只作历史说明，**不得当作当前规则引用**。

## 0.27 — brief + handoff（2026-09-07）

- `taskctl.py brief TASK-xxx --role worker|scout|verifier`：从 manifest + round.json 生成标准简报，供 M1 贴进真窗或子代理。
- `taskctl.py handoff`：输出 M1 接班简报（任务状态、attempt、未闭环、BLOCKERS、hook-runs 摘要、下一步）。
- 成功标准：工人 / 新 M1 只凭生成物执行，不依赖手写转述。负向：缺失 task、非法 role 拒绝。两条命令都不改状态。

## 0.26 — 规则收敛（2026-09-07）

- SKILL.md 只保留当前规则；0.20～0.25 历史块移入本文件；G0～G3 / schema / transition 表移入 `references/task-gate.md`。
- 确认 `verification_policy()` 为 Gate、`audit-round`、`transition`、M1 收口的唯一策略实现；窗口轴与任务轴分离写进当前规则。
- 策略无法唯一决定收口路径，或 manifest 自相矛盾时输出 `POLICY_CONFLICT`，并有正负向测试。
- 立刻补丁（不另占版本）：测试用 `Path(__file__).parent / "taskctl.py"` 自解析；新增 `taskctl.py selftest`；`hooks.md` 每宿主补一行能力确认测试命令。

## 0.25 — 并行协作与加分判定

- 可用宿主已有子代理减少传话；不新增角色或窗号；M1 仍是唯一收口。
- 加分改为「本窗已确认的额外能力」；加分句从默认开场拿掉；未确认禁止自称加分。
- G2 忽略 `taskctl` 自管文件：`manifest.json` / `rerun.json` / `verify-report.json` 未列入工人 `changed_files` 不算漏报。
- 路径归一化只去掉 `./` 前缀，保留 `.task/` 这类点目录名。

## 0.24 — Hook 可观测性

- `hook-audit` 写 `phase=start` 与 `phase=end`，共用 `run_id`；字段含 `host`、`source`、`project_root`、`round_id`。
- `.task/hook-runs.jsonl` 最多保留 100 行，应 gitignore。
- Hook 失败只记账，不 `transition`、不标 done。

## 0.23 — 证据可重跑

- Full Gate / `verified` / `integrated` / `done` 时真跑验收命令，核 evidence 路径。
- 有 Git 时核工作区：落在 `allowed_paths` 内但未申报 → FAIL。
- 结果写入 `.task/TASK-xxx/rerun.json`。口头 PASS 无效。`--basic` 不重跑。

## 0.22 — 任务门禁

- 引入 `.task/TASK-xxx/manifest.json`、R 编号、`taskctl.py`。
- 状态机：`pending → in_progress → worker_done → (verifying → verified) → integrated → done`。
- P1：低风险且 `attempt < 2` 允许 M1 短路径 `worker_done → integrated`。
- P2：`--root` 必须在子命令前面。
- P3：`status` 在窗口行旁标注任务终态，不改写 `window_status`。
- P4：各宿主 Stop hook 经适配层调用 `hook-audit`。

## 0.20 / 0.21

- 多窗口编号、Registry / RECEIPT-LOG、斥候→主力→搜剿、卡点上限 4、查收以本窗重跑为准。
- 0.21 起有可复制的 `taskctl.py` 雏形；0.22 起成为正式任务控制层。
