# Hook 适配说明

宿主差异只允许写在本文件与各宿主适配器脚本。`SKILL.md` 与 `taskctl.py` 不按宿主名分支核心行为。

## 重要边界

`taskctl.py` 是检查逻辑；hook 只是生命周期触发器。`hook-audit` 写入 `.task/hook-runs.jsonl`：每次触发一对 `start`/`end`，带同一 `run_id`。不自动创建验证 agent，也不替 M1 标记 `done`。Hook 失败只记账。没有 `.task/` 时静默跳过。

该 jsonl **最多 100 行**，应加入 `.gitignore`，不要提交进 Git。

**能力确认（所有宿主同一标准）：** 新窗口用下面该宿主段的测试命令跑一次。本窗实际收到结果才算确认。未确认一律默认，禁止报加分。确认便宜，纪律不松：加分后仍须本窗重跑验收，仍须 `transition` 收口。

## Codex

Codex 使用项目级 `.codex/hooks.json` 或用户级 `~/.codex/hooks.json`。建议先使用项目级配置。把 `{SKILL_ROOT}` 换成本机 Codex 技能目录（升级系列隔离副本为 `...\skills\multi-window_M-0.31`）：

```json
{
  "description": "multi-window_M task audit",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "py -3 \"{SKILL_ROOT}\\scripts\\taskctl.py\" hook-audit --source codex-stop --host codex",
            "commandWindows": "py -3 \"{SKILL_ROOT}\\scripts\\taskctl.py\" hook-audit --source codex-stop --host codex",
            "timeout": 30,
            "statusMessage": "Auditing multi-window tasks"
          }
        ]
      }
    ]
  }
}
```

自动记录 `source=codex-stop`、`host=codex`。手动调用默认 `source=manual`。不要只凭手动 `audit-round` 当作 Hook 证据。`hook_supervision=true` 时，收口只认宿主 stop 的 start/end 对。

**能力确认测试命令：** 在本窗派一个只回复 `CAP-OK` 的子代理（或打开另一则已有对话并引用一句原文）。本窗收到 `CAP-OK` 或那句原文 → 将该项记为已确认；都没收到 → 默认，不要报加分。能力确认状态：待实测。

## Cursor

事件名是 `stop`，不要抄 Codex 的 JSON。用户级：

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "py -3 C:\\Users\\user\\.cursor\\hooks\\multi-window-m-hook-audit.py",
        "timeout": 30
      }
    ]
  }
}
```

适配器调用本技能的 `taskctl.py`：

```text
py -3 <SKILL_ROOT>\scripts\taskctl.py --root <工作区> hook-audit --source cursor-stop --host cursor
```

隔离副本的 `SKILL_ROOT` 例：`C:\Users\user\.cursor\skills\multi-window_M-0.31`。

- 不要设 `failClosed`。适配器始终退出码 0。
- 诊断日志：`C:\Users\user\.cursor\hooks\last-cursor-stop.log`（无 `.task/` 也写）。
- 用户级 cwd 往往是 `~/.cursor/`。项目里再放 `.cursor/hooks.json` 同一条 command。
- 证明触发：jsonl 新增同一 `run_id` 的 `start`+`end`，且 `source=cursor-stop`。禁止手动 `--source cursor-stop` 冒充。

**能力确认测试命令：** 派一个子代理，令其只回复 `CAP-OK`。本窗收到该原文 → 「子代理回传」已确认；收不到则未确认。能打开并引用**另一对话**的原文 → 「跨对话可见」已确认。Cursor 默认同窗看不见其他对话；**能派子代理本身不是加分**。能力确认状态：待实测（不要凭产品说明书报加分）。

## Zcode

```text
py -3 <SKILL_ROOT>\scripts\taskctl.py --root <项目根> hook-audit --source zcode-stop --host zcode
```

接线：`~/.zcode/cli/config.json` 的 `hooks.Stop` 必须 `enabled: true`。适配器建议恒退出码 0、清空 stdout，并写 `last-zcode-stop.log`。不把 Zcode 的 JSON 写死在此文件。

**能力确认测试命令：** 派一个子代理，令其只回复 `CAP-OK`。本窗收到该原文 → 「子代理回传」已确认。另测：能否引用旧对话原文。能力确认状态：子代理回传已实测（2026-09-07）；关联旧对话原文待闭环。

## 无 hook 宿主

暂不为 dsh 配置。人工：`py -3 scripts/taskctl.py audit-round`。无 hook 不等于不能收口：走默认流程，**不要打开** `hook_supervision`，以免 `HOOK_EVIDENCE_MISSING` 卡死最低挡。也不要把缺 hook 日志当成完成证据。

**能力确认测试命令：** 无。按默认，不要报加分。

## 触发约定

M1 派工创建 `round.json`；窗口完成后 receipt；全部查收信号后 `check_requested: true`。hook 只在该字段为 true 时做 round 检查，但只要有 `.task/` 就会写 start/end 记录。
