# Multi-Window M · DSH 0.33

**长需求对齐 → M1 分工 → 多窗口执行 → M1 重跑验收 → 集成与体验。**

> 本分支已恢复 DSH 0.33 适配版。此前的 0.35 以及 0.34～0.36 后续迭代退出推荐使用范围；历史保留在提交与 archive 标签中，不要按版本号自动升级。

[完整项目介绍与流程图](https://github.com/tmddwyaaa-source/multi-window_M/tree/main) · [独立安装包](https://github.com/tmddwyaaa-source/multi-window_M/releases/latest) · [当前规则](SKILL.md) · [话术](templates.md)

## 这份是什么

以 0.33 为基础的 DSH 适配版，增加 DSH 证据桥、接线样例与相关测试。包含已记录的接线补丁，不宣称是未经改动的初始快照。没有后续 packet、合同基线、round-close 或 0.34 独立验收双路由命令。

M1 负责对齐需求、拆任务、定边界、安排批次、查收与集成；常驻 M2～M10 维护长期模块，临时 C 窗处理短任务（每轮最多四个）。用户按安排开窗并通知查收，最后体验项目。

## 保留的任务管理与验收机制

这份适配版保留完整的 0.33 协作和检查层：

- 任务与窗口状态分开，.task 是启用任务控制后的状态来源，状态视图由脚本生成。
- 需求原文映射到 R 项，工人交付覆盖项、证据、改动和自测；已知缺口不能冒充完成。
- G0～G3 检查材料、要求覆盖、命令重跑与路径证据，以及策略要求的独立验收。
- 低风险满足条件走短路径，中高风险或累计重开达到阈值走独立验收；只有 M1 集成和最终收口。
- 整轮回执和审计核对本轮窗口与任务；单项 PASS 或检查跳过不等于全局完成。
- 同一卡点第一次原人修、第二次真换人、第三次硬停；保留失败和交接记录。
- brief 生成任务指示，handoff 汇总未闭环与下一步；迁移先备份再检查。
- 斥候按未知/冲突/过期信息触发，避免重复探索；回报与读取预算仍按试验配置看待。

完整中文解释、流程图和全部默认命令见 [main 首页](https://github.com/tmddwyaaa-source/multi-window_M/tree/main#核心机制任务怎样被确认完成)。字段和 DSH 命令以本分支 [task-gate.md](references/task-gate.md) 为准。脚本规则不等于权限隔离，独立验收也可能漏验，最终体验仍由用户确认。

## 安装

下载本分支或独立 DSH 压缩包，将 SKILL.md、templates.md、CHANGELOG.md、scripts/、references/ 放在完整文件夹 `~/.dsh/skills/multi-window-m-033/`。目录名与技能 name 保持一致，然后在 DSH 中显式加载 `/multi-window-m-033`。

主窗口：“你是 M1，先看看你对需求的理解，对齐后再安排窗口批次。”

执行窗口：“我是 M2，请完成指示任务。”

查收：“M2、C1 已完成，请查收。”

## Hook 与能力边界

Hook 需单独接线，下载不会自动安装。按 [dsh-evidence.md](references/dsh-evidence.md) 配置当前 profile 并验证事件。将 [配置样例](scripts/dsh-hooks.example.json) 的 `{SKILL_ROOT}` 改成本机绝对路径；其他作者路径只作示例。

本包历史适配规定 DSH 使用默认能力、人工路由，子代理只作受限辅助，不担任正式工人、替换负责人或独立验收者。该规定不代表当前所有 DSH 版本的统一能力，使用时需核验实际环境。

通用 hooks.md 的“暂不为 dsh 配置”是保留的旧通用段落；本适配包的接线以 dsh-evidence.md 和实际桥脚本为准。诊断事件、手工运行、Hook 退出成功都不自动代表已获得有效监督证据。

## 验证范围

```text
py -3 scripts/taskctl.py selftest
```

本次仓库恢复前，DSH 0.33 的 11 套脚本自检通过；没有重做真实多窗口项目验收。核心 taskctl.py 与本地来源哈希一致。命令退出成功不能证明需求行为全部完成。

已使用后续版本的项目先备份；本包不提供新格式降级转换。不要直接覆盖 .task 或已有项目工具。

Codex 默认版，以及 Cursor、Zcode 的流程与历史接线范围，见 [main 首页](https://github.com/tmddwyaaa-source/multi-window_M/tree/main)。本分支仅提供 DSH 安装内容。
