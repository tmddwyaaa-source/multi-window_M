# multi-window_M

多窗口协作 skill：常驻 M1～M10，临时 Cn。版本号只写在 `SKILL.md` 开头的 `version` 字段，不写进文件夹名或仓库名。

当前 `version`：**0.25**

## 安装

把本仓库复制到：

- Cursor：`~/.cursor/skills/multi-window_M/`
- Codex：`~/.codex/skills/multi-window_M/`

对话里用 `/multi-window_M` 调用。不要用带版本号的文件夹名。

旧项目升级脚本（`--root` 必须在子命令前面）：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
```

Hook 配置见 `references/hooks.md`。

## 仓库里有什么

| 路径 | 说明 |
|------|------|
| `SKILL.md` | 规则与当前版本 |
| `templates.md` | 话术与模板 |
| `scripts/taskctl.py` | 任务门禁 |
| `references/hooks.md` | Cursor / Codex / Zcode Hook |
| `testdata/deskkit-sandbox/` | 0.25 回归沙盒（含 TEST-RECORD、docs、.task） |
| `testdata/records/` | 测试记录副本 |

`testdata` 里的沙盒文件夹也不用版本号当名字。它测的是 skill 当时的 `version` 字段。

## 本机归档

本机仍可保留 `multi-window_M-0.22` … `multi-window_M-0.25` 作为只读旧版。日常只用无版本号的 `multi-window_M`。
