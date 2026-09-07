# multi-window_M

多窗口协作 skill：常驻 **M1～M10**，临时 **Cn**（一轮最多 4 个）。用共享文档（模块注册表）和 `taskctl.py` 门禁，让多个 Agent 窗口在同一项目上分工，而不是靠窗口之间互相看见聊天记录。

仓库目录名固定为 `multi-window_M`。**当前版本写在 `SKILL.md` 开头的 `version` 字段**（现为 **0.26**）。升级系列期间，skill 的 `name` 与本机隔离文件夹带版本号（`multi-window_M-0.26`），避免覆盖上一版。

对话里调用：`/multi-window_M-0.26`

## 当前版本：0.26 — 规则收敛

**要解决的唯一问题：** 说明书按 0.22～0.25 分层堆叠，Agent 容易引用过期块；策略表虽已进脚本，但缺少「冲突必拒绝」的专项覆盖。

**这版改了什么：**

- `SKILL.md` 只描述**当前规则**。0.20～0.25 的历史说明移到 [`CHANGELOG.md`](CHANGELOG.md)，不得当作当前规则引用。
- G0～G3、manifest schema、`transition` 表移到 [`references/task-gate.md`](references/task-gate.md)。
- `verification_policy()` 是 Gate / `audit-round` / `transition` / M1 收口的**唯一**策略实现。窗口状态（`round.json`）和任务状态（`manifest.json`）必须分开写。
- 无法唯一决定收口路径，或 manifest 自相矛盾（无效 `risk`、显式 `independent_verification` 与 risk/attempt/flag 打架）→ 输出 `POLICY_CONFLICT` 并停止。有正负向测试。
- 立刻补丁：测试脚本用 `Path(__file__).parent / "taskctl.py"` 自解析；新增 `taskctl.py selftest`；`hooks.md` 每个宿主补一行能力确认测试命令。
- 核心行为宿主无关：宿主差只写在 `references/hooks.md` 与适配器，不写进 `SKILL.md` / `taskctl.py` 的分支逻辑。

**这版没改：** 窗号规则、状态机合法边、Hook 三不（不改状态、不派工、不标 done）、证据格式、加分判定语义。

一键自检：

```text
py -3 scripts/taskctl.py selftest
```

## 它做什么

1. **M1** 写大纲和 `docs/MODULE-REGISTRY.md`，把需求拆成任务（`.task/TASK-xxx/manifest.json` + R 编号）。
2. 用户开工人窗（常驻 Mn 或临时 Cn），工人只改 `allowed_paths`，自己跑验收命令。
3. 用户把「{窗号} 已完成」送到 M1。M1 **本窗重跑**同一条命令，再 `transition` 收口。
4. 低风险且失败次数 `< 2`：短路径 `worker_done → integrated → done`。其余必须独立验收窗。
5. Stop Hook（可选）只记账到 `.task/hook-runs.jsonl`，**不**替 M1 标 done。

查收永远以本窗重跑为准。子代理回传、关联会话只能当线索。

## 安装（升级系列：隔离副本）

不要覆盖已经在用的 `multi-window_M`（0.25）。复制本仓库到带版本号的文件夹：

| 宿主 | 建议路径 | 调用 |
|------|----------|------|
| Cursor | `~/.cursor/skills/multi-window_M-0.26/` | `/multi-window_M-0.26` |
| Codex | 升级系列完成前**不要同步**；完成后由维护者手动复制 | — |

旧项目把门禁脚本拷进仓库（`--root` 必须在子命令前面）：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
```

Hook 接线、各宿主能力确认测试命令：[`references/hooks.md`](references/hooks.md)。

## 默认与加分

所有宿主从**默认**开始。加分只看**这一窗已经确认**的额外能力（例如能引用其他对话原文，或能把子代理结果收回本会话），不看产品说明书。未确认不要说加分。确认方法见 `hooks.md` 各宿主段的「能力确认测试命令」。

加分只改变协作方式（可少传话），**不**放松验收：仍须本窗重跑，仍须 `transition`，仍须 M1 唯一收口。

## 仓库里有什么

| 路径 | 说明 |
|------|------|
| `SKILL.md` | 当前规则；`name` / `version` 现为 0.26 |
| `CHANGELOG.md` | 0.20～0.26 历史；不是当前规则 |
| `templates.md` | 开场、派工、查收、Registry 等复制即用话术 |
| `scripts/taskctl.py` | 任务门禁；`selftest` / `gate` / `transition` / `hook-audit` |
| `scripts/test_*.py` | 路径归一化、Hook 可观测性、策略冲突；由 `selftest` 调用 |
| `references/task-gate.md` | G0～G3、schema、策略表、命令 |
| `references/hooks.md` | Codex / Cursor / ZCode 适配与能力确认 |
| `testdata/deskkit-sandbox/` | 0.25 回归沙盒（TEST-RECORD、docs、`.task`） |
| `testdata/records/` | 历史测试记录副本 |

`testdata` 沙盒目录也不用版本号当名字。它记录的是当时跑通的 `version`。`.task/hook-runs.jsonl` 在 `.gitignore` 里，不上传。

## 常用命令

```text
py -3 scripts/taskctl.py selftest
py -3 scripts/taskctl.py --root <项目根> status
py -3 scripts/taskctl.py --root <项目根> gate TASK-001
py -3 scripts/taskctl.py --root <项目根> audit-round
py -3 scripts/taskctl.py --root <项目根> transition TASK-001 worker_done --actor worker
```

`--root` 放在子命令后面会被拒绝。细节见 `references/task-gate.md`。

## 版本约定

| 位置 | 规则 |
|------|------|
| GitHub 仓库名 / 根目录 | 始终 `multi-window_M` |
| `SKILL.md` 的 `version` | 唯一权威版本号 |
| 升级系列本机文件夹与 `name` | `multi-window_M-0.26` 这类隔离副本，不覆盖上一版 |
| 系列全部完成后 | 再考虑改回无版本号的日常 `name`，并手动同步 Codex |

本机可同时保留 `multi-window_M`（0.25）和 `multi-window_M-0.26`。测新版本请调用 `/multi-window_M-0.26`。

## 后续路线（尚未做）

统一升级路线：v0.27 brief/handoff → v0.28 文档变视图 → v0.29 需求覆盖 → v0.30 挡位正式化 → v0.31 迁移治理。下一版只做一件事，过发布门禁再继续。

## 许可证与隐私

本仓库默认按 GitHub 仓库可见性发布。不要把含密钥的 `.env`、Hook 诊断日志或 `hook-runs.jsonl` 提交上来。
