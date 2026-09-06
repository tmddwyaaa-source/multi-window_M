# 查收记录（RECEIPT-LOG）

> **主导窗口 M1** 维护。标题只用窗号（M2 / M3 / C1），不用文件名当窗名。

ROUND-002 已查收并收口。标题只用窗号 C1，不用 CB1。

## 2026-09-06 查收 — M2

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ `src/greet.py`、`.task/TASK-001/worker-report.json`、evidence |
| 本窗重跑验收 | ✅ `py -3 tests/test_greet.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ❌ Full Gate：`changed_files` 未申报 `.task/TASK-001/manifest.json`（transition 改过该文件） |

**结论**：`fail`
**阻塞项**：把 `.task/TASK-001/manifest.json` 写入 `worker-report.json` 的 `changed_files`；不要手改 `manifest.status`。改完后对 M1 再说「M2 已完成，请查收」。

## 2026-09-06 查收 — M3

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ `src/counter.py`、`.task/TASK-002/worker-report.json`、evidence |
| 本窗重跑验收 | ✅ `py -3 tests/test_counter.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ❌ `manifest.status` 仍是 `pending`，未执行 `transition TASK-002 worker_done --actor worker`；`status_history` 里的 in_progress 被整文件覆盖丢掉 |

**结论**：`fail`
**阻塞项**：不要整份覆盖 `manifest.json`。M1 已重新 `in_progress`。M3 只跑 `py -3 scripts/taskctl.py transition TASK-002 worker_done --actor worker`。不要写 `verify-report.json`（那是 C1）。不要标 verified/done。完成后对 M1 说「M3 已完成，请查收」。

## 2026-09-06 二次查收 — M2

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ |
| 本窗重跑验收 | ✅ `py -3 tests/test_greet.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ✅ `changed_files` 已含 `manifest.json`；Full Gate PASS；`integrated` → `done` |

**结论**：`done`（TASK-001 已收口）
**阻塞项**：无。整轮仍未齐（M3/C1）。

## 2026-09-06 二次查收 — M3

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ |
| 本窗重跑验收 | ✅ `py -3 tests/test_counter.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ❌ `transition worker_done` 已做成；Full Gate 仍漏报 `.task/TASK-002/manifest.json`。缺 verify-report 是 C1 的活，这次不因此打回 |

**结论**：`fail`
**阻塞项**：和 M2 上次一样：把 `.task/TASK-002/manifest.json` 加进 `worker-report.json` 的 `changed_files`。不要重写 bump，不要写 verify-report。完成后对 M1 说「M3 已完成，请查收」。C1 仍等这次过后再开。

## 2026-09-06 三次查收 — M3（skill 补丁后）

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ |
| 本窗重跑验收 | ✅ `py -3 tests/test_counter.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ✅ `worker_done`；basic gate PASS。Full Gate 只剩缺 `verify-report.json`（C1） |

**结论**：`内容通过`（TASK-002 仍是 worker_done，未 verified）
**阻塞项**：无。请开 C1 做独立验收。整轮未齐。

## 2026-09-06 查收 — C1

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ `.task/TASK-002/verify-report.json`；`reviewer=C1` ≠ M3；R1–R3 pass；未改 `src/` |
| 本窗重跑验收 | ✅ Registry 的 `py -3 -c ...` → `RESULT PASS`；`py -3 tests/test_counter.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ❌ `verifying` 已做；`verified` 未做成。Full Gate：`changed_files` 未申报 C1 写的 `verify-report.json` |

**结论**：`fail`（窗口验收命令过了，任务不能收口）
**阻塞项**：G2 用工人的 `worker-report.changed_files` 对账，却把验收人的 `verify-report.json` 当成漏报。C1 按规定不能改 worker-report；M3 也不该申报自己没写的验收报告。这不是让 C1 重做判断。整轮未齐。

## 2026-09-07 查收 — C1（G2 补丁后）

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ `verify-report.json`；`reviewer=C1` ≠ M3 |
| 本窗重跑验收 | ✅ Registry 命令 → `RESULT PASS`；`py -3 tests/test_counter.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ✅ `verified`；Full Gate PASS；M1 `integrated` → `done` |

**结论**：`done`
**阻塞项**：无。ROUND-001：`BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE`。临时窗 C1 本轮关闭。无网页可预览，跳过。

## 2026-09-07 查收 — C1

产出文件：`data/words-batch/CB1.json`（不是窗号）

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ 10 词；`window=CB1`；`owner_window=C1`；worker-report.window=C1 |
| 本窗重跑验收 | ✅ `py -3 tests/test_cb1.py` → `RESULT PASS` |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ |
| Registry 交付物 | ✅ worker_done；Full Gate PASS；短路径 `integrated` → `done` |

**结论**：`done`
**阻塞项**：无。ROUND-002：`ROUND_READY_TO_CLOSE`。C1 本轮关闭。Hook 日志仍未出现。无网页可预览，跳过。
