# Hook 适配说明

## 重要边界

`taskctl.py` 是检查逻辑；hook 只是生命周期触发器。v0.25 的 `hook-audit` 仍按 v0.24 写入 `.task/hook-runs.jsonl`：每次触发一对 `start`/`end`，带同一 `run_id`。不自动创建验证 agent，也不替 M1 标记 `done`。Hook 失败只记账。没有 `.task/` 时静默跳过。

该 jsonl **最多 100 行**，应加入 `.gitignore`，不要提交进 Git。

## Codex

Codex 使用项目级 `.codex/hooks.json` 或用户级 `~/.codex/hooks.json`。建议先使用项目级配置。把 `{SKILL_ROOT}` 换成 `C:\Users\user\.codex\skills\multi-window_M-0.25`：

```json
{
  "description": "multi-window_M v0.25 task audit",
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

自动记录 `source=codex-stop`、`host=codex`。手动调用默认 `source=manual`。不要只凭手动 `audit-round` 当作 Hook 证据。

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

适配器调用：

```text
py -3 C:\Users\user\.cursor\skills\multi-window_M-0.25\scripts\taskctl.py --root <工作区> hook-audit --source cursor-stop --host cursor
```

- 不要设 `failClosed`。适配器始终退出码 0。
- 诊断日志：`C:\Users\user\.cursor\hooks\last-cursor-stop.log`（无 `.task/` 也写）。
- 用户级 cwd 往往是 `~/.cursor/`。项目里再放 `.cursor/hooks.json` 同一条 command。
- 证明触发：jsonl 新增同一 `run_id` 的 `start`+`end`，且 `source=cursor-stop`。禁止手动 `--source cursor-stop` 冒充。

## Zcode

```text
py -3 C:\Users\user\.cursor\skills\multi-window_M-0.25\scripts\taskctl.py --root <项目根> hook-audit --source zcode-stop --host zcode
```

不把 Zcode 的 JSON 写死在此文件。

## 无 hook 宿主

暂不为 dsh 配置。人工：`py -3 scripts/taskctl.py audit-round`

## 触发约定

M1 派工创建 `round.json`；窗口完成后 receipt；全部查收信号后 `check_requested: true`。hook 只在该字段为 true 时做 round 检查，但只要有 `.task/` 就会写 start/end 记录。
