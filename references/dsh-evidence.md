# dsh 证据账本：接线、验收、回滚

这是**方案 B**（dsh 当一等宿主）的落地步骤。规则本身写在 `SKILL.md` 与 `references/hooks.md`；
本文件只讲"怎么装上、怎么证明它真的在工作、怎么撤掉"。

前置事实（已核对 dsh 0.1.5-rc.1 源码与包文档）：

- dsh 没有自己的 hook 方言，靠 `@deepseek-ai/dsh-hooks-claude-code` / `-codex` 两个桥执行别的工具的 `hooks.json`。
- **只有 Claude Code 桥暴露 `SubagentStop`**；Codex 桥静默丢弃 `SubagentStart` / `SubagentStop`。所以必须选 Claude Code 桥。
- 桥是 profile 插件，**没挂桥时 dsh 照常启动**，只是没有 hook 证据。挂桥是外部进程，桥脚本崩不会让 dsh 起不来。
- dsh 的 `Stop` 是能阻塞的串行监听器，`stop_hook_active` 恒 `false`，连续阻塞上限未实现
  ⇒ **hook 命令必须恒退出 0**，否则每一步都会强制再来一轮，自锁。
- 桥的 stdin 载荷带 `hook_event_name` / `session_id` / `cwd`（`transcript_path` 恒为空串）；
  `SubagentStop` 的 `session_id` 是**子会话 id**，`agent_id` 是子代理 id。

## 一、装了没装：先自检

```powershell
# 1. 技能脚本自身健康（应打印 SELFTEST PASS）
py -3 "$env:USERPROFILE\.dsh\skills\multi-window-m-035\scripts\taskctl.py" selftest

# 2. 桥接脚本能不能被拉起（无 .task/ 时应打印 HOOK_AUDIT_SKIP 且退出码 0）
'{"hook_event_name":"Stop","session_id":"dry-run","cwd":"."}' |
  py -3 "$env:USERPROFILE\.dsh\skills\multi-window-m-035\scripts\dsh-hook-bridge.py"
```

第 2 条即使失败也**只报错不炸 dsh**：桥接捕获所有异常并恒退出 0。

## 二、挂桥（改的是你自己的 dsh，可回滚）

在启动 dsh 的那个 profile 的用户 patch 层加一行。web profile 是
`%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`：

```yaml
# 你的 profile patch 层：在每个 bundle 层之后应用
- name: '@deepseek-ai/dsh-hooks-claude-code'
  config:
    configPath: C:\Users\user\.dsh\skills\multi-window-m-035\scripts\dsh-hooks.example.json
```

- `configPath` 在进程启动时读一次；相对路径按**启动 dsh 的目录**解析，所以用绝对路径最稳。
- 改完要**重启 dsh**（profile 插件行不热重载）。之后只改 `cordis.patch.yml` 是热重载。
- 别把 hook 桥挂进 `~/.dsh/cordis.patch.yml`（home 层），那会影响所有 profile；除非你确实想全局生效。

接线样例 `scripts/dsh-hooks.example.json` 里的三个事件：

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "... dsh-hook-bridge.py --record --status \"${CLAUDE_PROJECT_DIR}\\.task\\dsh-hook-status.json\"" }] }],
    "Stop":         [{ "hooks": [{ "type": "command", "command": "... dsh-hook-bridge.py" }] }],
    "SubagentStop": [{ "matcher": "general-purpose", "hooks": [{ "type": "command", "command": "... dsh-hook-bridge.py" }] }]
  }
}
```

`${CLAUDE_PROJECT_DIR}` 与 `${CLAUDE_PLUGIN_ROOT}` 由桥做**字符串替换**（不是 shell 展开），
并被设为 hook 进程的环境变量；省略 `--root` 时桥接退回到载荷里的 `cwd`（= 会话工作区）。

**一个要记住的坑：** dsh 的 `SubagentStop` 载荷里 `session_id` 是**子会话 id**（不是父会话）。
所以子代理结束写的 `run_id` 是那个子会话 id，能满足 `hook_supervision`，但**不能**直接拿它当"哪个 M 窗"的证据。
要用它证 A 挡的回传，必须让 M1 把 `round.json` 的 `subagents[].window` 和账本里的 `agent_id`
对上——桥接在载荷没有 `agent_id` 时会把子会话 id 填进 `agent_id`，就是这个用途。

## 三、怎么证明它真的在工作（三关）

**第 1 关：dsh 侧收到了事件。** 看状态文件（只有配了 `--status` 才有）：

```powershell
Get-Content <项目根>\.task\dsh-hook-status.json -Encoding UTF8
```

应看到 `"event": "Stop"`、`"session_id": "<该窗会话号>"`、`"result": "recorded"`。
没有这个文件或 `result` 是 `no-taskctl` → 桥路径或 `.task/` 不在。

**第 2 关：账本里出现了完整一对。** dsh 的事件是一次性的，所以桥接**一次调用就写完 start+end**：

```powershell
Get-Content <项目根>\.task\dsh-runs.jsonl -Encoding UTF8 | Select-Object -Last 2
```

应看到同一 `run_id`（= `session_id`）的 `"phase": "start"` 与 `"phase": "end"`，
且 `"source"` 是 `dsh-stop` 或 `dsh-subagent-end`、`"host": "dsh"`。
`.task/hook-runs.jsonl` **不应该**出现 dsh 的行——两本账本分开。

**第 3 关：收口门禁认这对证据。**

```powershell
py -3 scripts/taskctl.py --root <项目根> audit-round
```

在 `hook_supervision: true` 且其它门禁都过的前提下，不应再出现 `HOOK_EVIDENCE_MISSING`。
反例（必须仍然失败，用来证明门禁没被放水）：

- 只有 `dsh-session-start` 的 `record` 单条 → 仍 `HOOK_EVIDENCE_MISSING`
- 只有 `--source manual` → 仍 `HOOK_EVIDENCE_MISSING`
- 完全没有账本 → 仍 `HOOK_EVIDENCE_MISSING`

这三条都有自动用例：`py -3 scripts/test_dsh_bridge.py`（16 项，含 5 项负向）。

## 四、回滚

按影响从大到小：

| 情况 | 做法 |
|---|---|
| 只想停掉 dsh 证据 | 从 `cordis.patch.yml` 删掉那两行（`- name:` + `config:`），重启 dsh |
| 桥接脚本本身有问题 | 改 `dsh-hooks.example.json` 的 `command`，或把整个 `hooks` 置空并重启 |
| 挂桥后 dsh 起不来 | 见下：这几乎不会是桥造成的，但仍按此顺序排查 |
| 插件把 profile 搞坏了 | `dsh --profile web --dump-default-config`（跳过用户 patch 层）→ 定位；`dsh --profile rescue` 起最小 profile；`dsh plugin --profile web remove <pkg>`；或直接编辑 `profiles/web/package.json` 的 `dsh.profile.bundles` |
| 想完全恢复原状 | 删掉 patch 层里那两行 + 删 `.task/dsh-runs.jsonl` / `.task/dsh-hook-status.json`；技能目录改回原名即可 |

**dsh 起不来的真正机制**（与桥无关，别误诊）：`assertEntriesLoaded` / `assertEntriesActivated`
要求每个启用行都能 import 且激活成功，失败即 `installFailLoud` → `process.exit(1)`。
hook 桥只增加**外部进程调用**，最坏情况是那条 hook 失败并被记日志。

## 五、四条不许违反的边界

1. **hook 恒退出 0**（桥已保证）。dsh 没有连续阻塞上限，非 0 = 自锁。
2. **hook 只写证据**：不 `transition`、不改 `window_status`、不标 done。裁决权仍在 `taskctl.py`。
3. **两本账本都要 gitignore**：`.task/hook-runs.jsonl`、`.task/dsh-runs.jsonl`（用到再加 `.task/dsh-hook-status.json`）。
4. **只有产品本身没有 hook 触发点的宿主**（dsh 不在其列）才不要打开 `hook_supervision`；否则 `HOOK_EVIDENCE_MISSING` 会卡死最低挡。

## 六、DSH 的挡位与委派限制（0.33 新增）

**挡位固定：** DSH 上 `gears` 写 `capability=default`、`collaboration=P`、`capability_confirmed=false`。
即使已接 hooks 桥（证据账本可用），**协作挡仍是 P**：DSH 的 M1 不能自行开窗、窗口之间也不可见，所以"加分/挡 A"在 DSH 上不成立。**`hook_supervision` 是否打开是独立判断**（见第四节），不要和挡位混为一谈。

**委派限制（硬规则）：** DSH 子代理**读不到本 skill**——`disable-model-invocation: true` 让它不进模型目录，而子代理是模型不是用户，连手工 `/` 入口都没有。因此：

| 角色 | 子代理能否担任 | 原因 |
|---|---|---|
| 受限斥候 / 检索 / 低风险辅助 | ✅ 可以 | 只需读，回报有上限 |
| 正式工人（写交付物 + 交 worker-report） | ❌ 不得 | 读不到纪律，实测会需求缺失、反复打回 |
| 替换负责人（第 2 级阶梯） | ❌ 不得 | 换人必须是"真正不同的独立执行上下文"；换个提示重新发不算换人 |
| verifier / 独立验收 | ❌ 不得 | 独立性不足，且同样读不到纪律 |

**派子代理时必须自带任务胶囊**（格式见 [../templates.md](../templates.md)）：目标 / 允许路径 / 禁止事项 / 验收命令 / 回报上限 / 身份限制，**不超过 15 行**。这是替代"skill 自动导入"的唯一可行手段。

**因此 DSH 上第 2 级阶梯通常走不到**：没有合格的新执行者时，写 `BLOCKERS` 的 `NO_ELIGIBLE_REPLACEMENT_WORKER`，请 M1 / 用户开新的合格 M/C 窗口——**不得伪造换人**，也不要拿子代理顶替 owner。

**宿主能力清单**（决定某宿主能否支撑挡位）：见桌面文档 `multi-window优化-分析与因果.md` §5，逐项确认后再下判断。
