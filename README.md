# multi-window_M

多窗口协作 skill：常驻 **M1～M10**，临时 **Cn**（一轮最多 4 个）。用共享文档（模块注册表）和 `taskctl.py` 门禁，让多个 Agent 窗口在同一项目上分工，而不是靠窗口之间互相看见聊天记录。

仓库目录名固定为 `multi-window_M`。**当前版本写在 `SKILL.md` 开头的 `version` 字段**（现为 **0.29**）。升级系列期间，skill 的 `name` 与本机隔离文件夹带版本号（`multi-window_M-0.29`），避免覆盖上一版。

对话里调用：`/multi-window_M-0.29`

## 当前版本：0.29 — 需求覆盖

**要解决的唯一问题：** 用户口头加需求、测试仍绿，但有条目从未做成 R 项（没漏测但漏做）。

**这版改了什么：**

- manifest 必须有 `source_refs`：每条原始需求 `id` / `text` / `maps_to`（指向已有 R 编号）。每条 R 也必须被某条 source 映射到。
- `round.json` 可选 `source_requirements`。`audit-round` / Full Gate 发现未映射需求输出 `REQUIREMENT_COVERAGE_FAIL` 并停止收口。
- 用户新增需求必须走「来源记录 → R 项 → 验收命令」，禁止只改聊天话术。
- 负向：缺 `source_refs`、`maps_to` 为空、映射到不存在的 R、round 列出但任务未收录 → `REQUIREMENT_COVERAGE_FAIL`。

**这版没改：** 窗号、状态机、Hook 三不、`brief` / `handoff`、`status --markdown`、`POLICY_CONFLICT`、加分判定。

一键自检：

```text
py -3 scripts/taskctl.py selftest
```

应包含 `test_requirement_coverage.py`。

## 它做什么

1. **M1** 写大纲和 `docs/MODULE-REGISTRY.md`（路径/交付物），把用户原始需求写入 `source_refs`，再拆成带验收命令的 R 项。
2. 状态总览用 `status --markdown --write` 生成 `docs/TASK-STATUS.md`，不要在 Registry 里另写一套 pending/done。
3. M1 跑 `brief`，把生成物（含来源 → R）交给工人窗。工人只改 `allowed_paths`，自己跑验收命令。
4. 用户把「{窗号} 已完成」送到 M1。M1 **本窗重跑**同一条命令，再 `transition` 收口。出现 `REQUIREMENT_COVERAGE_FAIL` 则停止，先补映射。
5. 用户中途加需求：先写 `source_requirements` / 对应任务 `source_refs` 并补 R 与 verify，禁止只改聊天。
6. 换对话接班：新 M1 先跑 `handoff` 并读 `TASK-STATUS.md`。
7. 低风险且失败次数 `< 2`：短路径 `worker_done → integrated → done`。其余必须独立验收窗。
8. Stop Hook（可选）只记账，**不**替 M1 标 done。

查收永远以本窗重跑为准。子代理回传、关联会话只能当线索。

## 安装（升级系列：隔离副本）

不要覆盖已经在用的 `multi-window_M`、`multi-window_M-0.26`、`multi-window_M-0.27` 或 `multi-window_M-0.28`。复制本仓库到带版本号的文件夹：

| 宿主 | 建议路径 | 调用 |
|------|----------|------|
| Cursor | `~/.cursor/skills/multi-window_M-0.29/` | `/multi-window_M-0.29` |
| Codex | 升级系列完成前**不要同步**；完成后由维护者手动复制 | — |

旧项目把门禁脚本拷进仓库（`--root` 必须在子命令前面）：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
```

Hook 接线、各宿主能力确认测试命令：[`references/hooks.md`](references/hooks.md)。

## 默认与加分

所有宿主从**默认**开始。加分只看**这一窗已经确认**的额外能力，不看产品说明书。未确认不要说加分。确认方法见 `hooks.md`。

加分只改变协作方式，**不**放松验收：仍须本窗重跑，仍须 `transition`，仍须 M1 唯一收口。

## 仓库里有什么

| 路径 | 说明 |
|------|------|
| `SKILL.md` | 当前规则；`name` / `version` 现为 0.29 |
| `CHANGELOG.md` | 0.20～0.29 历史；不是当前规则 |
| `templates.md` | 开场、brief/handoff、Registry；含 `source_refs` 示例 |
| `scripts/taskctl.py` | 门禁；覆盖校验 / `brief` / `handoff` / `status --markdown` / `selftest` |
| `scripts/test_*.py` | 含 `test_requirement_coverage.py`；由 `selftest` 调用 |
| `references/task-gate.md` | G0～G3、schema、`REQUIREMENT_COVERAGE_FAIL`、命令 |
| `references/hooks.md` | Codex / Cursor / ZCode 适配与能力确认 |
| `testdata/` | 历史沙盒与测试记录 |

`.task/hook-runs.jsonl` 在 `.gitignore` 里，不上传。

## 常用命令

```text
py -3 scripts/taskctl.py selftest
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role worker
py -3 scripts/taskctl.py --root <项目根> handoff
py -3 scripts/taskctl.py --root <项目根> status --markdown --write
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
| 升级系列本机文件夹与 `name` | `multi-window_M-0.29` 这类隔离副本，不覆盖上一版 |
| 系列全部完成后 | 再考虑改回无版本号的日常 `name`，并手动同步 Codex |

测新版本请调用 `/multi-window_M-0.29`。

## 后续路线（尚未做）

统一升级路线：v0.30 挡位正式化 → v0.31 迁移治理。下一版只做一件事，过发布门禁再继续。

## 许可证与隐私

不要把含密钥的 `.env`、Hook 诊断日志或 `hook-runs.jsonl` 提交上来。
