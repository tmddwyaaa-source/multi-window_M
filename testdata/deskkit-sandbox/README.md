# DeskKit（skill 稳定性沙盒）

本仓库用于测试 **multi-window_M v0.25** 的派工、门禁、短路径收口与独立验收，而不是做一个完整产品。

本轮只做两块互不重叠的 Python 模块：问候语 `greet`、计数器 `bump`。实现分别由 M2、M3 完成；C1 只验收 M3。

## 多窗口分工

| 文档 | 用途 |
|------|------|
| docs/MODULE-REGISTRY.md | 模块清单与状态 |
| docs/RECEIPT-LOG.md | 查收记录 |
| docs/FIX-PLAN.md | Phase 2 修复分工 |
| docs/BLOCKERS/ | 卡点升级报告（达 4 次上限） |

**作战条令**：常驻 M1～M10；临时 C 一轮最多 4。禁止 M11+、禁止 CB 当窗号。查收以本窗重跑为准；工人不会自动交 M1。未确认额外能力不要说加分。网页预览仅 M1。可用宿主已有子代理加速，不新增角色窗。
