# multi-window_M

多窗口协作 skill：常驻 **M1～M10**，临时 **Cn**（一轮最多 4 个）。用共享文档（模块注册表）和 `taskctl.py` 门禁，让多个 Agent 窗口在同一项目上分工，而不是靠窗口之间互相看见聊天记录。

仓库目录名固定为 `multi-window_M`。**当前版本写在 `SKILL.md` 开头的 `version` 字段**（现为 **0.30**）。升级系列期间，skill 的 `name` 与本机隔离文件夹带版本号（`multi-window_M-0.30`），避免覆盖上一版。

对话里调用：`/multi-window_M-0.30`

## 当前版本：0.30 — 挡位正式化 + 证据收紧

**要解决的唯一问题：** 低挡假装高挡，或声称有 hook 监督却没有宿主 stop 日志，仍把任务标完成。

**这版改了什么：**

- 开局自报三套挡位（能力 / 协作 / 风险）。`round.json` 增加 `gears`、`hook_supervision`、`subagents`。
- 未确认却写 bonus 或协作挡 A → `GEAR_VIOLATION`。加分不放松验收。
- `hook_supervision=true` 时，`audit-round` 收口必须有宿主 stop（`cursor-stop` / `codex-stop` / `zcode-stop`）的 start/end 对。手动 `audit-round` / `--source manual` 不能替代 → `HOOK_EVIDENCE_MISSING`。
- 子代理必须绑定 `task_id` / `window` / `allowed_paths` / `run_id`。同文件并发、重复、工人兼 verifier、越权 → `PARALLEL_FAIL`。
- Hook 仍三不：不改状态、不派工、不标 done。无 hook 宿主不要打开 `hook_supervision`。

**这版没改：** 窗号、状态机转移表、目录结构、`source_refs`、`brief` / `handoff`、`status --markdown`。

一键自检：

```text
py -3 scripts/taskctl.py selftest
```

应包含 `test_gears_hook.py`。

## 它做什么

1. **M1** 开局自报挡位，写大纲和 Registry，把需求写入 `source_refs` 再拆成 R 项。
2. 状态总览用 `status --markdown --write` 生成 `docs/TASK-STATUS.md`。
3. M1 跑 `brief`，把生成物交给工人窗。工人只改 `allowed_paths`。
4. 用户把「{窗号} 已完成」送到 M1。M1 **本窗重跑**，再 `transition` 收口。出现 `GEAR_VIOLATION` / `HOOK_EVIDENCE_MISSING` / `PARALLEL_FAIL` 则停止。
5. 派出子代理时写入 `subagents` 绑定；未确认能力时协作挡保持 P。
6. 换对话接班：新 M1 先跑 `handoff`。
7. Stop Hook（可选）只记账，**不**替 M1 标 done。声明了 hook 监督就必须有宿主 stop 日志。

查收永远以本窗重跑为准。子代理回传、关联会话只能当线索。

## 安装（升级系列：隔离副本）

不要覆盖已经在用的 `multi-window_M` 或 `multi-window_M-0.26`～`0.29`。复制本仓库到带版本号的文件夹：

| 宿主 | 建议路径 | 调用 |
|------|----------|------|
| Cursor | `~/.cursor/skills/multi-window_M-0.30/` | `/multi-window_M-0.30` |
| Codex | 升级系列完成前**不要同步**；完成后由维护者手动复制 | — |

旧项目把门禁脚本拷进仓库（`--root` 必须在子命令前面）：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
```

Hook 接线、各宿主能力确认测试命令：[`references/hooks.md`](references/hooks.md)。

## 默认与加分

所有宿主从**默认 + 协作挡 P** 开始。加分只看**这一窗已经确认**的额外能力。未确认不要说加分，也不要把 `gears` 写成 bonus / A。

加分只改变协作方式，**不**放松验收：仍须本窗重跑，仍须 `transition`，仍须 M1 唯一收口。

## 仓库里有什么

| 路径 | 说明 |
|------|------|
| `SKILL.md` | 当前规则；`name` / `version` 现为 0.30 |
| `CHANGELOG.md` | 0.20～0.30 历史；不是当前规则 |
| `templates.md` | 开场自报挡位、brief/handoff、`gears` 示例 |
| `scripts/taskctl.py` | 门禁；挡位 / hook 证据 / 子代理绑定 / `selftest` |
| `scripts/test_*.py` | 含 `test_gears_hook.py`；由 `selftest` 调用 |
| `references/task-gate.md` | G0～G3、schema、`GEAR_VIOLATION` / `HOOK_EVIDENCE_MISSING` / `PARALLEL_FAIL` |
| `references/hooks.md` | Codex / Cursor / ZCode 适配与能力确认 |
| `testdata/` | 历史沙盒与测试记录 |

`.task/hook-runs.jsonl` 在 `.gitignore` 里，不上传。

## 常用命令

```text
py -3 scripts/taskctl.py selftest
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role worker
py -3 scripts/taskctl.py --root <项目根> handoff
py -3 scripts/taskctl.py --root <项目根> status --markdown --write
py -3 scripts/taskctl.py --root <项目根> audit-round
py -3 scripts/taskctl.py --root <项目根> hook-audit --source cursor-stop --host cursor
```

`--root` 放在子命令后面会被拒绝。细节见 `references/task-gate.md`。

## 版本约定

| 位置 | 规则 |
|------|------|
| GitHub 仓库名 / 根目录 | 始终 `multi-window_M` |
| `SKILL.md` 的 `version` | 唯一权威版本号 |
| 升级系列本机文件夹与 `name` | `multi-window_M-0.30` 这类隔离副本，不覆盖上一版 |
| 系列全部完成后 | 再考虑改回无版本号的日常 `name`，并手动同步 Codex |

测新版本请调用 `/multi-window_M-0.30`。

## 后续路线（尚未做）

统一升级路线：v0.31 迁移治理。下一版只做一件事，过发布门禁再继续。

## 许可证与隐私

不要把含密钥的 `.env`、Hook 诊断日志或 `hook-runs.jsonl` 提交上来。
