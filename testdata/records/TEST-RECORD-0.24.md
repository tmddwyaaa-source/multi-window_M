# multi-window_M-0.24 测试记录

- **项目**：`D:\Cursor_projectt\test-v0.24-cursor`
- **被测 skill**：multi-window_M-0.24
- **记录人**：M1
- **日期**：2026-09-06（正文时间戳为 UTC；本地 UTC+8）
- **本文件**：项目主目录 `TEST-RECORD.md`

## 当前局面（先看这里）

ROUND-001 / ROUND-002 / **ROUND-003 均已收口**。

| 项 | 状态 |
|----|------|
| TASK-004 clip / M2 | **done**（attempt=2 经 C1 独立验收） |
| TASK-007 / C1 | **done**（本轮关闭） |
| Hook 100 行 | ROUND-003 派工前 M1 已测 PASS |

ROUND-003 测到：`attempt=1` 仍可短路径；`attempt=2` 短路径 `integrated` 被拒；C1 补 verify-report 后 `verified → integrated → done`。



---

## A. ROUND-001（已关闭，通过）

目的：冒烟。无 Git。窗口 M2、M3、C1。

| 任务 | 路径 | 结果 |
|------|------|------|
| TASK-001 low | `worker_done → integrated → done` | PASS |
| TASK-002 medium | 缺 verify-report 时 Full Gate FAIL；C1 补上后 `verifying → verified → integrated → done` | PASS（含预期失败） |
| TASK-003 | 短路径 done；`reviewer=C1` ≠ `M3` | PASS |
| `audit-round` | `BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE` | PASS |

M1 重跑：`test_echo.py` / `test_tally.py` / verify-report 断言均为 `RESULT PASS`。

Hook（当时）：jsonl 成对 `start/end`，`host=cursor`，`source=cursor-stop`，`round_id=ROUND-001`。只记账，不替 M1 标 done。

细节时间线见 `docs/RECEIPT-LOG.md` 下半；`round.json` 已归档 `.task/archive/ROUND-001.json`。

---

## B. ROUND-002（进行中）

目的：Git 门禁、路径越界、非法窗号。reopen 本拍不做。

| 任务 | 窗口 | 要测的 |
|------|------|--------|
| TASK-004 | M2 | `clip(text, n)`；先漏列 `tests/test_clip.py` 应 FAIL，补全应 PASS |
| TASK-005 | M3 | `pad(text, width)`；不得改 echo/tally/clip |
| TASK-006 | C1 | 搜剿上两项（尚未派开工） |

M1 派工前已测：`round-init … M11` / `CB1` 均 `FAIL: invalid window id(s)`，未创建 round.json。证据：`.task/archive/illegal-window-ids.txt`。

已做 Git 基线提交，工作区对工人新文件可见。

### 第一次查收（M2、M3）

| 命令 | 结果 |
|------|------|
| `py -3 tests/test_clip.py` | PASS（`clip("hello",3)=="hel"` 等） |
| `py -3 tests/test_pad.py` | PASS |
| `gate TASK-004` | FAIL：`.task/TASK-004/worker-report.json` 已申报仍报未列入 |
| `gate TASK-005` | FAIL：同上路径误报；另有 `known_gaps` 非空被拒 |

Git 负向探针有效：漏列 `tests/test_clip.py` 时 gate 确实 FAIL。  
越界探针有效：工作区无 echo/tally/clip 被 M3 改动。  
正向 Git 探针失败：根因是脚本，不是 clip/pad 写错。

M3 把脚本 bug 写进 `known_gaps[]` 也不行：门禁规定该字段非空即 FAIL。应写 notes，不写 `known_gaps`。

工人改不了 `scripts/taskctl.py`。继续改 clip/pad 解不开这个卡点。

---

## C. 环境快照

| 项 | 值 |
|----|----|
| 宿主 | Cursor |
| 脚本 | `scripts/taskctl.py`（v0.24，迁入本仓库） |
| 当前 round | ROUND-002（`check_requested=false`） |
| Git | 有（ROUND-002 起）；ROUND-001 当时 `git=skipped` |
| 网页 | 无，预览跳过 |
| Hook | `.cursor/hooks.json` → 用户级适配器；jsonl 不入库 |

分工与契约：`docs/MODULE-REGISTRY.md`、`docs/LAB-SPEC.md`。查收原文：`docs/RECEIPT-LOG.md`。再下一拍候选：`docs/FIX-PLAN.md`。
