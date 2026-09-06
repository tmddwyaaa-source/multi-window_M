# 模块注册表（Module Registry）

> **主导窗口 M1** 维护。查收 / 打回只用窗号（M2～M10 或 C1…）。
> 常驻仅 M1～M10。临时 C 一轮最多 4 个，done 后关闭、号码可复用。
> 禁止新开 M11+，禁止 CB1 当窗号。

## 本轮派工
- 本轮窗口：C1
- 本阶段不使用：M2、M3、M4、M5、M6、M7、M8、M9、M10
- 临时窗上限：1（最多 4）

ROUND-001 已关闭。ROUND-002 复用临时窗号 C1（上一轮验收任务已 done，号码可复用）。
本轮不测 Hook 冒充；回合结束后若出现 `.task/hook-runs.jsonl`，由 M1 只读核对 start/end 是否成对。

本任务按循环渐进执行：
- 每环只做一个目标，必须有成功标准、证据、存档点
- 环内角色：斥候 → 主力 → 搜剿；fail 则主力↔搜剿
- 同一卡点签名最多 4 次；第 4 次仍 fail 必须硬停并写卡点升级报告
- 自测必须贴本回合终端原文；不要打开网页预览
- 常驻只称 M2～M10，临时只称 C1、C2…；不要用 CB1 / M11 当窗口号
- 工人跑完验收不会自动交给 M1；须用户传「{窗号} 已完成，请查收」

本任务除原有 Registry 规则外，使用 `.task/TASK-xxx/manifest.json` 的 R 编号。
只改 allowed_paths；结束时写 worker-report.json，逐条列出已覆盖的 R 编号、证据和已知遗漏。
worker_done 不等于 verified，不要自行标 done 或只口头说 PASS。
状态变更必须使用 taskctl.py transition，并填写正确 actor；不要直接编辑 manifest.status。

## 总览

| 编号 | 模块名 | 产出文件 | 负责窗口 | 状态 | 依赖 |
|------|--------|----------|----------|------|------|
| M1 | 主导 / 架构协调 | docs/、.task/ 编排 | **本窗口** | done | — |
| M2 | greet 问候语 | `src/greet.py` | M2 | done | ROUND-001，本轮不用 |
| M3 | bump 计数器 | `src/counter.py` | M3 | done | ROUND-001，本轮不用 |
| C1 | 词库批次 | `data/words-batch/CB1.json` | C1 | done | 号码复用；文件名 CB1 ≠ 窗号 C1 |

## 本轮要测的 skill 点

| 点 | 怎么测 |
|----|--------|
| 临时窗号码复用 | ROUND-001 的 C1 已 done；ROUND-002 再派 C1 |
| 文件名 ≠ 窗号 | 产出 `CB1.json`，查收只称 C1 |
| 低风险短路径 | TASK-003 risk=low |
| 验收命令真跑 | 工人贴终端原文；M1 本窗重跑同一条 |
| Hook 记账 | 不派人伪造 cursor-stop；若 Stop 触发再核对 jsonl |

## M2 — greet 问候语

**任务**：TASK-001（low，短路径）

**产出文件**：`src/greet.py`

**路径**：只改 `src/greet.py`、`.task/TASK-001/`。禁止改 `src/counter.py`、`tests/`、`docs/`、其他 TASK。

**交付物**：
- [x] `greet(name)`：非空名字返回 `Hello, {name}!`
- [x] 空字符串或纯空白 `name` 抛 `ValueError`
- [x] `.task/TASK-001/worker-report.json` 覆盖 R1、R2，evidence 路径真实存在
- [x] `py -3 scripts/taskctl.py transition TASK-001 worker_done --actor worker`

**验收**（检查已有文件，不生成文件）：
```text
py -3 tests/test_greet.py
```
成功时该命令只打一行 `RESULT PASS`。

**环内角色**：斥候 → 主力 → 搜剿；同一卡点最多 4 次

**开工话术**：`我是 M2 窗口，请读 MODULE-REGISTRY.md 中【M2】章节。按斥候→主力→搜剿执行。只改规定路径。必须贴终端输出原文。不要打开网页预览。`

**状态**：`done`

## M3 — bump 计数器

**任务**：TASK-002（medium，必须独立验收）

**产出文件**：`src/counter.py`

**路径**：只改 `src/counter.py`、`.task/TASK-002/worker-report.json`、`.task/TASK-002/evidence/`。禁止改 `src/greet.py`、`tests/`、`docs/`、`verify-report.json`。

**交付物**：
- [x] `bump(n)`：`n` 为 `int` 且 `n >= 0` 时返回 `n + 1`
- [x] `n < 0` 抛 `ValueError`；`bool` / `float` 抛 `TypeError`（`True` 不能当 1）
- [x] `.task/TASK-002/worker-report.json` 覆盖 R1、R2、R3
- [x] transition：`worker_done --actor worker`。不要自己标 verified / done

**验收**：
```text
py -3 tests/test_counter.py
```
成功时该命令只打一行 `RESULT PASS`。

**环内角色**：斥候 → 主力 → 搜剿；同一卡点最多 4 次

**开工话术**：`我是 M3 窗口，请读 MODULE-REGISTRY.md 中【M3】章节。按斥候→主力→搜剿执行。只改规定路径。必须贴终端输出原文。不要打开网页预览。`

**状态**：`done`

## 归档 — ROUND-001 的 C1（TASK-002 独立验收）

**产出文件**：`.task/TASK-002/verify-report.json`（这是报告文件，不是窗号）

**路径**：只写 `verify-report.json`。禁止改 `src/`、`tests/`、M3 的 worker-report。

**依赖**：必须等 M3 已完成并请查收之后再写 pass。开窗后若 M3 未交工，先当斥候只读，不要提前 pass。

**交付物**：
- [x] 只读核对 manifest、worker-report、diff、测试证据
- [x] `reviewer` 必须是 `C1`，不得等于 M3
- [x] `checked_requirements` 含 R1、R2、R3；`result` 为 `pass` 才可请查收
- [x] transition：`verifying` 然后 `verified`，`--actor verifier`

**验收**：
```text
py -3 -c "import json; r=json.load(open('.task/TASK-002/verify-report.json',encoding='utf-8')); assert r.get('reviewer')=='C1'; assert str(r.get('result','')).lower()=='pass'; assert set(r.get('checked_requirements',[]))>=set(['R1','R2','R3']); print('RESULT PASS')"
```

**开工话术**：`我是 C1 窗口，请读 MODULE-REGISTRY.md 中【C1】章节。当前角色：独立验证，只读，禁止修改实现代码。`

**状态**：`done`（ROUND-001 已关闭）

## C1 — 词库批次 CB1

**任务**：TASK-003（low，短路径）

**产出文件**：`data/words-batch/CB1.json`（脚本字段 `"window": "CB1"`，不是窗号）

**路径**：只写 `data/words-batch/CB1.json`、`.task/TASK-003/`。禁止改 `src/`、`tests/`、`docs/`、TASK-001/002。

**交付物**：
- [x] 8～12 条词，每条有非空 `en`、`zh`
- [x] `"window": "CB1"`（文件标记）；`"owner_window": "C1"`（真实窗号）
- [x] `.task/TASK-003/worker-report.json` 覆盖 R1、R2、R3
- [x] `py -3 scripts/taskctl.py transition TASK-003 worker_done --actor worker`
- [x] 对 M1 只说「C1 已完成」，不要说「CB1 已完成」

**验收**（检查已有文件，不生成文件）：
```text
py -3 tests/test_cb1.py
```
成功时该命令只打一行 `RESULT PASS`。规格见 `data/CONTENT-SPEC.md`。

**环内角色**：斥候 → 主力 → 搜剿；同一卡点最多 4 次

**开工话术**：`我是 C1 窗口，请读 MODULE-REGISTRY.md 中【C1】章节。按斥候→主力→搜剿执行。只改规定路径。必须贴终端输出原文。不要打开网页预览。`

**状态**：`done`（ROUND-002 临时窗关闭）
