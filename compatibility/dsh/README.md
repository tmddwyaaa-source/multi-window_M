# DSH 0.33 适配版

独立技能文件夹：[multi-window-m-033](multi-window-m-033/)。基线是 0.33，加 DSH 接线适配和后补记录；没有后续 0.34 的双路由及合同机制。

## 安装

把整个 multi-window-m-033 文件夹复制到 `~/.dsh/skills/`，保持目录名与 skill name 一致。在 DSH 显式加载 `/multi-window-m-033`，先和 M1 对齐，再按安排开窗。不要把默认 Codex 文件覆盖进此包，也不要同一任务混用两套适配文件。

## 与默认版的差异

- 调用名使用 DSH 的小写连字符形式，语义版本仍为 0.33。
- 增加 dsh-hook-bridge.py、Hook 配置样例和桥接测试。
- DSH 证据在 .task/dsh-runs.jsonl，与其他宿主分账。
- 本包历史约定使用默认能力、人工路由；子代理不充当正式工人、替换负责人或独立验收者，详见专用参考。
- CHANGELOG 有 SessionStart 接线补丁，不称为未经改动的初始快照。

这些约定不代表当前所有 DSH 版本的统一能力，请验证实际 profile、插件和事件。

## Hook 与自检

详见 [dsh-evidence.md](multi-window-m-033/references/dsh-evidence.md)。配置样例不是安装器：将 `{SKILL_ROOT}` 替换为本机技能目录绝对路径，再挂对应 profile 桥接插件并验证事件。文档中的作者本机路径只是示例。

没有有效证据时不声明监督开启。Hook 不改状态、不派工、不标 done。

```text
py -3 <本机DSH技能目录>/scripts/taskctl.py selftest
```

规则见 [SKILL.md](multi-window-m-033/SKILL.md)，完整项目用法见 [主仓库首页](../../README.md)。脚本通过不等于真实多窗项目已验证。
