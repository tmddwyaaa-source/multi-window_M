# TEST-RECORD — multi-window_M v0.25 / ROUND-001

> 对齐日期：2026-09-06  
> 项目：`D:\Cursor_projectt\test-v0.25-cursor`（DeskKit 沙盒）  
> 主导：M1  
> 本轮窗口：M2、M3、C1

这份文件给**人看**：发生了什么、skill 测到了什么、你下一步做什么。  
正式查收仍以 `docs/RECEIPT-LOG.md` 和 `scripts/taskctl.py` 的终端结果为准。

---

## 一句话结论

**功能代码已经写好，测试也过了。**  
卡住的不是 `greet` / `bump` 写错，而是任务门禁（谁改了哪些文件、状态怎么改）没走完。

**不要让 M2/M3 从零重做。不要现在开 C1。**  
只要他们各补一小步，再对 M1 说「已完成，请查收」。

---

## 这轮在测什么

| 编号 | 窗口 | 任务 | 风险 | 收口路径 | 要测的 skill 点 |
|------|------|------|------|----------|-----------------|
| TASK-001 | M2 | `src/greet.py` 问候语 | low | 短路径：worker_done → M1 integrated → done | 工人实现 + G2 Git 对账 |
| TASK-002 | M3 | `src/counter.py` 计数器 | medium | 必须独立验收：worker_done → C1 verifying → verified → M1 收口 | 禁止工人自己标完；C1 不能等于 M3 |
| （验收） | C1 | 只写 `verify-report.json` | — | 等 M3 真正 worker_done | 临时窗 + G3 独立验收 |

M2 和 M3 文件不重叠，可以并行。C1 依赖 M3 交工。

---

## 时间线

1. **M1 派工**：空仓库搭 DeskKit 骨架、验收脚本、`.task/`、Registry。TASK-001/002 进入 `in_progress`。
2. **用户开 M2、M3** 干活（本窗未代写实现）。
3. **用户对 M1 说「M2/M3 已完成，请查收」**。
4. **M1 本窗重跑**（不以口头为准）：
   - `py -3 tests/test_greet.py` → `RESULT PASS`
   - `py -3 tests/test_counter.py` → `RESULT PASS`
5. **Full Gate 没过**，打回，没有标 done。
6. **M1 把 TASK-002 重新 `in_progress`**（因为状态被覆盖回 `pending`，工人无法直接 `worker_done`）。

---

## 查收明细

### M2 / TASK-001 — 功能过，门禁 fail

| 项 | 结果 |
|----|------|
| `src/greet.py` | 有。空/空白抛 ValueError，否则 `Hello, {name}!` |
| 本窗重跑 `py -3 tests/test_greet.py` | `RESULT PASS` |
| `worker-report.json` + evidence | 有，R1/R2 覆盖 |
| `transition worker_done` | 已做，`status=worker_done` |
| Full Gate | **FAIL** |

失败原文：

```text
RESULT FAIL
- TASK-001: git changes in allowed_paths not listed in changed_files: .task/TASK-001/manifest.json
```

原因（人话）：  
工人跑 `transition` 时，脚本改了 `.task/TASK-001/manifest.json`（状态写成 worker_done）。  
v0.23 会看 Git：你改了允许路径里的文件，就必须写进 `worker-report.json` 的 `changed_files`。  
M2 的清单里有 `src/greet.py` 和 evidence，**漏了 `manifest.json`**。

补救（很小，不是重做功能）：

1. 打开 `.task/TASK-001/worker-report.json`
2. 在 `changed_files` 里加上：`.task/TASK-001/manifest.json`
3. 不要手改 `manifest.status`
4. 再跑 `py -3 tests/test_greet.py`，对 M1 说「M2 已完成，请查收」

### M3 / TASK-002 — 功能过，状态机 fail

| 项 | 结果 |
|----|------|
| `src/counter.py` | 有。`type(n) is int`，负数 ValueError，bool/float TypeError |
| 本窗重跑 `py -3 tests/test_counter.py` | `RESULT PASS` |
| `worker-report.json` + evidence | 有，R1/R2/R3 覆盖 |
| `gate TASK-002 --basic` | PASS |
| Full Gate | FAIL（缺 C1 的 `verify-report.json`，这步本来就不是 M3 的） |
| `transition worker_done` | **没做成** |

查收当时：`manifest.status` 仍是 `pending`，`status_history` 只剩创建时那一条。  
更早 M1 做过 `in_progress`，但文件被整份覆盖，记录丢了。  
规则是：状态只能 `taskctl.py transition` 改，不能当普通 JSON 整文件重写。  
从 `pending` 不能直接跳到 `worker_done`，所以工人当时即使想 transition 也会被拒。

M1 已补：

```text
py -3 scripts/taskctl.py transition TASK-002 in_progress --actor M1
```

当前：`TASK-002 status = in_progress`。

补救（很小，不是重做 bump）：

1. 不要再整份覆盖 `manifest.json`
2. 不要写 `verify-report.json`（那是 C1）
3. 只跑：`py -3 scripts/taskctl.py transition TASK-002 worker_done --actor worker`
4. 再跑 `py -3 tests/test_counter.py`，对 M1 说「M3 已完成，请查收」

若之后 Full Gate 又报 `changed_files` 漏了 `manifest.json`，按 M2 同样补一行即可。

### C1 — 还没到

C1 要验收的是「M3 已经 worker_done 的 TASK-002」。  
M3 状态还不是 `worker_done`，C1 现在写 pass 会变成假验收。

**先不要继续 C1。** 等 M1 第二次查收 M3 通过后，再进 C1。

---

## 你要做什么（操作清单）

| 顺序 | 谁 | 做什么 | 不要做什么 |
|------|----|--------|------------|
| 1 | 你 → M2 窗 | 粘贴下面「M2 打回话术」，让它补 `changed_files` | 不要删掉 greet、不要重写测试 |
| 2 | 你 → M3 窗 | 粘贴下面「M3 打回话术」，让它只跑 transition | 不要重写 bump、不要写 verify-report |
| 3 | 你 → M1 窗 | 分别或一起说：`M2 已完成，请查收` / `M3 已完成，请查收` | 不要说「所有窗口已完成」（C1 还没做） |
| 4 | C1 | **等** M1 宣布 M3 内容通过 | 现在不要让 C1 写 pass |

### M2 打回话术（复制到 M2 窗）

```text
按打回项继续。不要重写 greet 逻辑。
把 .task/TASK-001/manifest.json 加进 worker-report.json 的 changed_files。
不要手改 manifest.status。
再跑：py -3 tests/test_greet.py
贴终端原文后，对 M1 说：M2 已完成，请查收。
```

### M3 打回话术（复制到 M3 窗）

```text
按打回项继续。不要重写 bump 逻辑，不要整份覆盖 manifest.json。
只跑：py -3 scripts/taskctl.py transition TASK-002 worker_done --actor worker
不要写 verify-report.json（那是 C1）。不要标 verified / done。
再跑：py -3 tests/test_counter.py
贴终端原文后，对 M1 说：M3 已完成，请查收。
```

---

## Skill 稳定性观察（到这一步）

| 点 | 结果 | 说明 |
|----|------|------|
| 窗号 M2/M3/C1 | 可用 | 没有误用 M11+ 或 CB 当窗号 |
| 文件边界 | 通过 | Git 里实现改动落在各自 allowed_paths |
| 验收命令真跑 | 通过 | M1 重跑都是 `RESULT PASS` |
| 口头完成 ≠ done | 通过 | M1 没有因「已完成」标 done |
| v0.23 Git `changed_files` | **打到了** | M2 漏报 `manifest.json`，Gate 真失败 |
| `transition` 状态机 | **打到了** | M3 覆盖 manifest 后卡在 pending；手改无效 |
| 短路径 vs 独立验收 | 尚未走完 | 要等 M2 补清单、M3 worker_done、C1 验收后才能测收口 |
| Hook `hook-runs.jsonl` | 尚未测 | 不冒充 cursor-stop；整轮过后再看 |
| 文件名 ≠ 窗号（如 CB1.json） | 本轮未排 | 记下，ROUND-002 再测 |

---

## 当前状态快照（2026-09-06 二次查收后）

| 对象 | 状态 |
|------|------|
| 窗口 M2 | **done**（TASK-001 短路径已收口） |
| 窗口 M3 | fail：worker_done 已做成，仍漏报 `manifest.json` |
| 窗口 C1 | **先别开** |
| TASK-001 | `done` / GATE_PASS |
| TASK-002 | `worker_done` / GATE_FAIL（漏报 manifest；缺 C1 verify-report） |
| 整轮 | 未齐 |

---

## 追加：2026-09-06 二次查收

### 本窗重跑

```text
py -3 tests/test_greet.py     → RESULT PASS
py -3 tests/test_counter.py   → RESULT PASS
py -3 scripts/taskctl.py gate TASK-001        → RESULT PASS
py -3 scripts/taskctl.py gate TASK-002        → RESULT FAIL
py -3 scripts/taskctl.py gate TASK-002 --basic → RESULT PASS
```

TASK-002 Full Gate 原文：

```text
RESULT FAIL
- TASK-002: missing .task\TASK-002\verify-report.json
- TASK-002: git changes in allowed_paths not listed in changed_files: .task/TASK-002/manifest.json
```

第一条是 C1 的活，这次不拿它打回 M3。  
第二条和 M2 上次相同：`transition` 改过 `manifest.json`，`changed_files` 没写上。不补这一行，C1 后面 `verified` 也会 Full Gate 失败。

### M2 收口（短路径测过了）

```text
receipt M2
transition TASK-001 integrated --actor M1
transition TASK-001 done --actor M1
→ TASK-001: done / GATE_PASS
```

低风险短路径可用：工人补齐申报后，M1 本窗重跑 Full Gate 再 `integrated` → `done`，不需要独立验收窗。

### 你现在做什么

只回 **M3 窗**（M2 不用再动，C1 先别动）：

```text
按打回项继续。不要重写 bump。
把 .task/TASK-002/manifest.json 加进 worker-report.json 的 changed_files。
不要写 verify-report.json。
再跑 py -3 tests/test_counter.py
完成后对 M1 说：M3 已完成，请查收。
```

---

## 追加：2026-09-06 skill 补丁 + M3 三次查收

### 我对这次修复的理解

上一轮把 M3 打回去补 `changed_files` 里的 `manifest.json`，是 **skill/G2 过严**，不是 bump 写错。

`transition` 改 `manifest.json` 是脚本记账，不是工人业务 diff。旧规则把这份文件当成「漏报」，工人只能反复补清单，还容易整份覆盖 manifest（M3 第一次就踩过）。

补丁（截图那个修复窗）做了：

1. G2 忽略 `taskctl` 自己写的 `.task/TASK-xxx/manifest.json` 和 `rerun.json`
2. `src/`、`tests/`、`worker-report`、evidence 仍必须申报；越界仍 FAIL
3. 文档写明：不要整份覆盖 manifest；不要把加分问句写进 Mn/C 开工话术
4. 单测 `G-pos-skip-manifest` 覆盖「不报 manifest 也能 PASS」

M1 已把本仓库 `scripts/taskctl.py` 从技能目录 `migrate-project --force` 过来。旧项目不升级脚本，门禁还是旧行为。

### 本窗重跑（升级脚本之后）

```text
py -3 tests/test_counter.py              → RESULT PASS
py -3 scripts/taskctl.py gate TASK-002 --basic → RESULT PASS
py -3 scripts/taskctl.py gate TASK-002         → RESULT FAIL
  只剩：missing .task\TASK-002\verify-report.json
  不再报 manifest.json 漏报
```

M3 **内容通过**。TASK-002 仍是 `worker_done`（medium，必须 C1）。M2 不用再动。

### 当前状态

| 对象 | 状态 |
|------|------|
| M2 / TASK-001 | done |
| M3 / TASK-002 | 内容通过，worker_done |
| C1 | **可以开工** |
| 整轮 | 未齐 |

---

## 追加：2026-09-06 查收 C1

### 本窗重跑

```text
py -3 tests/test_counter.py
→ RESULT PASS

py -3 -c "...verify-report.json... reviewer==C1 ... R1 R2 R3 ... print('RESULT PASS')"
→ RESULT PASS

py -3 scripts/taskctl.py gate TASK-002
→ RESULT FAIL
- TASK-002: git changes in allowed_paths not listed in changed_files: .task/TASK-002/verify-report.json
```

磁盘：`verify-report.json` 存在。`reviewer=C1`，不等于 M3。R1/R2/R3 都勾了，`result=pass`，`missing=[]`。C1 已 `transition verifying`，停在 `verifying`（`verified` 会再跑 Full Gate，过不了）。

### 判断

C1 **验收判断是对的**，不是没干活。  
卡住的是 G2：对账只看 **工人** 的 `changed_files`，但 `allowed_paths` 含整个 `.task/TASK-002/`，所以 C1 新写的 `verify-report.json` 被当成 M3 漏报。

和上一轮 `manifest.json` 同类：脚本/验收产物不该逼工人申报。manifest 已经进 `gate_owned_paths`；`verify-report.json` 还没有。

不能靠打回解决：

- 让 C1 改 `worker-report.json` → 违反 C1「禁止改工人报告」
- 让 M3 把 C1 的文件写进自己的 `changed_files` → 假申报
- M1 代改 → 未点名亲自微修订，且会掩盖 skill 缺口

因此：**不标 done，不假装 Full Gate 过。** TASK-002 不能 `verified` / `integrated`。整轮未齐。

### 建议（给 skill，不是给 C1 重做）

G2 忽略 `.task/TASK-xxx/verify-report.json`（与 manifest / rerun 同类），或验收人单独申报、且不占用工人 `changed_files`。

M2/M3/C1 都不必重写业务代码。等门禁补丁后再 `migrate-project --force`，由 C1 补跑 `transition verified`，M1 再收口。

---

## 追加：2026-09-07 G2 忽略 verify-report

本对话已导入新 skill。G2 现忽略 `manifest.json` / `rerun.json` / `verify-report.json`。M1 已 `migrate-project --force`。

```text
py -3 scripts/taskctl.py gate TASK-002
→ RESULT PASS
```

TASK-002 仍停在 `verifying`。C1 不必改 worker-report、不必重写报告。只需：

```text
py -3 scripts/taskctl.py transition TASK-002 verified --actor verifier
```

然后对 M1 说「C1 已完成，请查收」。M1 再 `integrated` → `done`。

---

## 追加：2026-09-07 查收 C1 并收口 ROUND-001

### 本窗重跑

```text
py -3 tests/test_counter.py
→ RESULT PASS

py -3 -c "...verify-report.json..."
→ RESULT PASS

py -3 scripts/taskctl.py gate TASK-002
→ RESULT PASS
```

C1 已 `verified`（`reviewer=C1`）。M1 收口：

```text
receipt C1
transition TASK-002 integrated --actor M1
transition TASK-002 done --actor M1
request-check
audit-round
→ BASIC_GATE_PASS
→ FULL_GATE_PASS
→ ROUND_READY_TO_CLOSE
```

| 对象 | 终态 |
|------|------|
| M2 / TASK-001 | done（短路径） |
| M3 / TASK-002 实现 | worker_done |
| C1 / TASK-002 验收 | verified 后由 M1 收口 |
| TASK-002 | done（独立验收路径） |
| ROUND-001 | 可关闭 |
| C1 窗 | 本轮关闭 |
| Hook `hook-runs.jsonl` | 本轮未出现，未测 |

独立验收路径已跑通：`worker_done → verifying → verified → integrated → done`。G2 忽略三份角色文件后，不再误打回。网页预览不适用，跳过。

ROUND-002 仍可测：文件名 ≠ 窗号（如 CB1.json）、Hook 成对记账。

---

## ROUND-002 开始：2026-09-07

本轮窗口只派 **C1**（号码复用）。TASK-003 低风险短路径。产出 `data/words-batch/CB1.json`，窗号仍是 C1。

Hook 不派人做；本轮结束后若有 `.task/hook-runs.jsonl`，M1 只读核对。

---

## 追加：2026-09-07 查收 C1 / ROUND-002 收口

用户说「C1 已完成」（未用 CB1 当窗号）。本窗重跑：

```text
py -3 tests/test_cb1.py
→ RESULT PASS

py -3 scripts/taskctl.py gate TASK-003
→ RESULT PASS
```

文件在 `data/words-batch/CB1.json`：10 词，`"window": "CB1"`，`"owner_window": "C1"`。工人报告 `window` 是 C1。

短路径收口后 `audit-round` → `ROUND_READY_TO_CLOSE`。TASK-003 `done`。C1 本轮关闭。

测过：临时窗号码复用、文件名 ≠ 窗号、查收只用窗号。  
未测：Hook（仍无 `.task/hook-runs.jsonl`）。

更新本文件：每次 M1 查收后再追加一节，不要用口头记录替代。
