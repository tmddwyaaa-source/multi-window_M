# multi-window_M

多窗口协作 skill：常驻 **M1～M10**，临时 **Cn**（一轮最多 4 个）。用共享文档（模块注册表）和 `taskctl.py` 门禁，让多个 Agent 窗口在同一项目上分工，而不是靠窗口之间互相看见聊天记录。

仓库目录名固定为 `multi-window_M`。**当前版本写在 `SKILL.md` 开头的 `version` 字段**（现为 **0.32**）。升级系列期间，skill 的 `name` 与本机隔离文件夹带版本号（`multi-window_M-0.32`），避免覆盖上一版。

对话里调用：`/multi-window_M-0.32`

## 当前版本：0.32 — 窗口身份收口

**要解决的唯一问题：** 窗号怎么写，收成一处；不新开窗类别。

**这版改了什么：**

- SKILL 增加唯一权威节「窗口身份（窗号）」。铁规则 1～2、对窗说话、反模式、templates 只指向该节。
- 合法窗号仍是 `M1`～`M10` 与 `C`+数字。词法以 `WINDOW_RE` / `valid_window()` 为准，文档是镜像。
- 不再把 `CBn` 当一种窗号来教。产出用普通文件名。
- `round-init` 仍拒绝非法窗号、重复窗号、一轮超过 4 个临时 C。不新增失败 token。

**这版没改：** 状态机、挡位、hook 证据、`source_refs`、`brief` / `handoff`、`status --markdown`、migrate-project 流程。Hook 仍三不。临时 C 上限仍是 4。

一键自检：

```text
py -3 scripts/taskctl.py selftest
```

应包含 `test_window_ids.py`。

## 它做什么

1. **M1** 开局自报挡位，写大纲和 Registry，把需求写入 `source_refs` 再拆成 R 项。
2. 旧项目先用已安装技能脚本 `migrate-project`（备份 + 报告 + lock），再 `--check` 等到 `MIGRATE_READY`。
3. 状态总览用 `status --markdown --write` 生成 `docs/TASK-STATUS.md`。
4. M1 跑 `brief` 派工。用户把「{窗号} 已完成」送到 M1。M1 **本窗重跑**，再 `transition` 收口。
5. Stop Hook（可选）只记账，**不**替 M1 标 done。

查收永远以本窗重跑为准。子代理回传、关联会话只能当线索。窗号规则见 SKILL「窗口身份」。

## 安装（升级系列：隔离副本）

不要覆盖已经在用的 `multi-window_M` 或 `multi-window_M-0.26`～`0.31`。复制本仓库到带版本号的文件夹：

| 宿主 | 建议路径 | 调用 |
|------|----------|------|
| Cursor | `~/.cursor/skills/multi-window_M-0.32/` | `/multi-window_M-0.32` |
| Codex | 升级系列完成后由维护者**手动**复制 | — |

旧项目把门禁脚本拷进仓库（`--root` 必须在子命令前面）：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --check
```

Hook 接线、各宿主能力确认测试命令：[`references/hooks.md`](references/hooks.md)。

## 默认与加分

所有宿主从**默认 + 协作挡 P** 开始。加分只看**这一窗已经确认**的额外能力。未确认不要说加分。加分不放松验收。

## 仓库里有什么

| 路径 | 说明 |
|------|------|
| `SKILL.md` | 当前规则；`name` / `version` 现为 0.32 |
| `CHANGELOG.md` | 0.20～0.32 历史；不是当前规则 |
| `templates.md` | 开场、brief/handoff、迁移命令 |
| `scripts/taskctl.py` | 门禁；`migrate-project` / `--check` / `selftest` |
| `scripts/test_*.py` | 含 `test_window_ids.py`；由 `selftest` 调用 |
| `references/task-gate.md` | G0～G3、schema、`MIGRATE_FAIL` / `MIGRATE_READY` |
| `references/hooks.md` | Codex / Cursor / ZCode 适配与能力确认 |
| `testdata/` | 历史沙盒与测试记录 |

`.task/hook-runs.jsonl` 在 `.gitignore` 里，不上传。

## 常用命令

```text
py -3 scripts/taskctl.py selftest
py -3 scripts/taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
py -3 scripts/taskctl.py --root <项目根> migrate-project --check
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role worker
py -3 scripts/taskctl.py --root <项目根> handoff
py -3 scripts/taskctl.py --root <项目根> audit-round
```

`--root` 放在子命令后面会被拒绝。细节见 `references/task-gate.md`。

## 版本约定

| 位置 | 规则 |
|------|------|
| GitHub 仓库名 / 根目录 | 始终 `multi-window_M` |
| `SKILL.md` 的 `version` | 唯一权威版本号 |
| 升级系列本机文件夹与 `name` | `multi-window_M-0.32` 这类隔离副本，不覆盖上一版 |
| 系列完成后 | 可考虑改回无版本号的日常 `name`，并手动同步 Codex |

测新版本请调用 `/multi-window_M-0.32`。

## 后续路线

0.32 只收窗口身份表述，不改行为。已知限制：Codex 未自动同步；跨宿主 hook 冒烟仍待实测。不要同一版本再混改角色、状态机或证据格式。不要未点名就把临时 C 上限改成 3。

## 许可证与隐私

不要把含密钥的 `.env`、Hook 诊断日志或 `hook-runs.jsonl` 提交上来。
