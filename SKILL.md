---
name: multi-window_M
description: >-
  多窗口分工：常驻仅 M1～M10；短期任务用临时窗 C1、C2（一轮最多 4 个）。
  禁止 M11+、禁止 CB1 当窗名。json 文件名 CB2 只给脚本认。
  默认不说加分；仅本窗已确认额外能力时才点名加分。
  可用宿主已有子代理减少传话，不新增角色窗；M1 唯一收口。
  查收以本窗重跑为准；证据可重跑；Hook 记 run_id/宿主/起止。
  斥候→主力→搜剿；卡点最多 4 次。
  在用户提到多窗口、M1、C1、查收、模块注册表、斥候/主力/搜剿时使用。
version: "0.25"
disable-model-invocation: true
---

# Multi-Window_M（常驻 M1～M10，临时 Cn）

当前版本写在本文件开头的 `version` 字段（现为 0.25），不要写进文件夹名。调用：`/multi-window_M`。

复制即用话术、Registry / 报告模板见 [templates.md](templates.md)。需要贴话术时先读该文件。窗口规则、策略表、证据重跑、Hook 记账沿用既有门禁。本版增加可选并行加速，并修正加分判定与出句。G2 对账忽略 `manifest.json` / `rerun.json` / `verify-report.json`。

## v0.25 并行协作

目标：在门禁已经稳定之后，减少用户传话，不改文件边界、独立验收和 M1 收口。

- 只用**当前宿主已经提供**的多代理 / 子代理。宿主没有就不要强用，不要发明新窗口角色或新编号（不新增 F、Scout 窗、M11+）。
- 默认流程仍是用户开 M/C 窗并传查收。并行是加速，不是新工作流。
- 子代理不是窗号。它干的活必须落在 Registry 里已有的 M/C 任务和 `allowed_paths` 上。
- 同一文件仍只让一个任务主改。不能用同一个子代理既当工人又当独立验收。
- 子代理回传说完成，只当查收**信号/线索**。M1 仍须本窗重跑验收命令，仍须 `transition` 收口。
- 未确认本窗有额外能力时，不要少开窗、少等用户传话，也不要说加分。

成功标准：并行只提高效率；G0～G3、短路径 / 独立验收策略表、窗号规则与 0.24 相同。

## v0.24 Hook 可观测性

`hook-audit` 每次在有 `.task/` 的项目里写两行：`phase=start` 与 `phase=end`，共用一个 `run_id`。字段包括 `host`、`source`、`event`、`project_root`、`round_id`、`exit_code`、`audit_result`。

- 日志文件：`.task/hook-runs.jsonl`，**只保留最新 100 行**。应加入 `.gitignore`，不要提交进 Git。
- 失败只记账，**不** `transition`、不改 manifest、不标 done。
- Cursor 用户级适配器调用本版 `taskctl.py`，并传 `--host cursor`。仍不要设 `failClosed`。
- 无 `.task/` 静默跳过。dsh 不接入。

从日志应能回答：何时、哪个宿主、哪个项目、哪一轮、这次检查结果是 ok 还是 fail。

## v0.23 证据重跑

Full Gate 与 `transition` 到 `verified` / `integrated` / `done` 时，脚本会：

1. 执行可识别的验收命令（`requirements[].verify_cmd`，或 `verify` 以 `py ` / `python ` / `pytest` 等开头，或 `worker-report.tests[].command`）。超时 60 秒或退出码非 0 → FAIL。自然语言的 `verify` 不执行。
2. `worker-report.evidence[].path`（或 `file`）必须存在于项目内。缺路径或文件不存在 → FAIL。
3. 有 `.git` 时读取工作区 diff / 未跟踪文件；落在本任务 `allowed_paths` 内但未写入 `changed_files` → FAIL。**例外：** `.task/TASK-xxx/manifest.json`、`rerun.json`、`verify-report.json` 不必列入工人 `changed_files`（脚本改状态、门禁写 rerun、验收人写报告，都不算工人漏报）。`src/`、`tests/`、`worker-report.json`、evidence 仍必须申报。无 Git 则 `rerun.json` 里 `git=skipped`，命令与 evidence 仍要过。路径只去掉 `./` 前缀，**保留** `.task/` 这类点目录名；申报与 Git 用同一套归一化后再比较。
4. 把时间、命令、退出码、diff 文件名写入 `.task/TASK-xxx/rerun.json`。口头 PASS 无效。

`--basic` 门禁不重跑。Hook 仍不标 done，也不因重跑失败去改状态。

## v0.22 任务门禁扩展

本节是在原有多窗口规则之上增加的任务控制层；没有 `.task/` 的项目仍按原流程工作。

### 任务清单与 R 编号

M1 继续接收用户的自然语言需求，但派工前必须为每个任务建立 `.task/TASK-xxx/manifest.json`，把需求拆成 `R1`、`R2`、`R3` 等验收项。R 编号属于任务，不属于窗口，也不要求为每个 R 单独创建文件。

建议的最小字段：`task_id`、`owner`、`track`、`risk`、`allowed_paths`、`requirements`、`attempt`。每个 requirement 至少包含 `id`、`text`、`verify`。可跑的验收命令写在 `verify_cmd`，或把 `verify` 写成可直接执行的命令。

### 状态与门禁

当项目启用 `.task/` 时，Gate、`transition`、M1 收口共用同一张策略表（`verification_policy()`）。禁止各自解释。规则冲突时脚本输出 `POLICY_CONFLICT` 并停止。

| 条件 | 独立验收 | 收口路径 |
|------|----------|----------|
| **low** 且 `attempt < 2` 且未设 `verification_required` | 否 | `worker_done` → `integrated`（`--actor M1`）→ `done` |
| **medium/high**，或 `attempt >= 2`，或 `verification_required` | 是 | `worker_done` → `verifying` → `verified` → `integrated` → `done` |

工人只能交 `worker_done`。需要独立验收时，仅 verifier 可交 `verifying` / `verified`。只有 M1 能 `integrated` / `done`。状态必须通过 `taskctl.py transition` 迁移，直接编辑 manifest 的状态不算有效收口。`verified`、`integrated`、`done` 迁移前脚本会重新运行 Full Gate；`done` 还必须从 `integrated` 进入。人工可将任务改为 `paused`、`blocked` 或 `reopened`，但不能无证据直接伪造 `verified` / `done`。

任务状态写在 `manifest.json`，窗口状态写在 `round.json` 的 `window_status`；不要把 `M2 verified` 当作任务完成。应写成 `TASK-001: verified`、`M2: worker_done`，避免把窗口和任务混为一谈。`taskctl status` 会在窗口行旁标注对应任务终态（全部 `done` 时带 `note=tasks_closed`），**不改写** `window_status`。

门禁分层推进：

- G0：任务目录、manifest、工人报告、独立验收报告是否存在。
- G1：R 编号是否完整覆盖，是否有对应证据。
- G2：测试退出码和 diff 越界检查；只允许改 `allowed_paths`。**v0.23 会真跑命令、核 evidence 路径、有 Git 时核工作区。** `manifest.json` / `rerun.json` / `verify-report.json` 未列入工人 `changed_files` 不算漏报。G3 仍要求独立验收报告存在且 reviewer ≠ 工人窗。
- G3：中高风险任务或连续失败任务必须有独立验收报告，且 `reviewer` 不得等于实现窗口。

v0.23 使用 `scripts/taskctl.py`，不依赖第三方 Python 包。常用命令：

```text
py -3 scripts/taskctl.py init TASK-001
py -3 scripts/taskctl.py gate TASK-001
py -3 scripts/taskctl.py audit-round
py -3 scripts/taskctl.py reopen TASK-001 --reason "人工验收发现遗漏"
py -3 scripts/taskctl.py status
py -3 scripts/taskctl.py migrate-project --destination scripts/taskctl.py --force
py -3 scripts/taskctl.py transition TASK-001 in_progress --actor M1
py -3 scripts/taskctl.py transition TASK-001 worker_done --actor worker
py -3 scripts/taskctl.py transition TASK-001 integrated --actor M1
py -3 scripts/taskctl.py transition TASK-001 done --actor M1
py -3 scripts/taskctl.py transition TASK-001 verifying --actor verifier
py -3 scripts/taskctl.py transition TASK-001 verified --actor verifier
```

前四行是 **low 且 attempt < 2** 的短路径（M1 查收时本窗重跑 Full Gate 后再 `integrated`）。后两行仅在策略表要求独立验收时使用；medium/high 或 `attempt >= 2` **禁止** `worker_done → integrated`。

脚本负责输出 `RESULT PASS` / `RESULT FAIL`；agent 不得只凭口头或自行打印 PASS。Hook 使用 `hook-audit`，会把每次 Stop 触发写入 `.task/hook-runs.jsonl`，从而区分“手动运行成功”和“Hook 实际触发”。

旧项目升级时，M1 必须先用已安装的本技能脚本执行 `migrate-project --destination scripts/taskctl.py --force`，再运行门禁。若项目没有 Git 且命令从子目录启动，**把 `--root` 放在子命令前面**：

```text
py -3 <本技能目录>\scripts\taskctl.py --root <项目根> migrate-project --destination scripts/taskctl.py --force
py -3 scripts/taskctl.py --root <项目根> status
```

`--root` 写在子命令后面会被拒绝。脚本不会再盲目继承父目录的 `.task/`。

### 任务难度与失败升级

所有任务都必须通过最低门禁。规模小不等于免检。是否独立验收只看上面的策略表：默认低风险且 `attempt < 2` 走 G0/G1/G2，由 M1 短路径收口；中高风险、人工重开或 `attempt >= 2` 必须走 G3。同一卡点第 4 次仍失败时写入 `docs/BLOCKERS/` 并停止盲改。

### Hook 边界

Hook 只是触发器，不是验收结论。一轮停止时调用 `hook-audit`（`--source` 按宿主：`codex-stop` / `cursor-stop` / `zcode-stop`，可选 `--host`）。没有 `.task/` 时静默退出。不自动创建 agent、不替 M1 标记 done。含 `.task/` 的项目触发后检查 `.task/hook-runs.jsonl`（start/end 成对，最多 100 行）。

Cursor：用户级 `~/.cursor/hooks.json` 的 `stop` 事件调用 `~/.cursor/hooks/multi-window-m-hook-audit.py`（转调本技能 `taskctl.py --host cursor`）。配置见 [references/hooks.md](references/hooks.md)。

### v0.22 收口判定

`audit-round` 现在按顺序输出基础结果和 Full Gate 结果：

- `BASIC_GATE_PASS` + `VERIFY_REQUIRED`：基础材料齐全，但仍需独立验收。
- `BASIC_GATE_PASS` + `FULL_GATE_FAIL`：有任务未通过，不能收口。
- `BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE`：本轮所有任务均通过，M1 才能逐项记录 `done`。

即使 Hook 输出成功，也不能替代 M1 本窗重新运行 Full Gate。

### 封闭状态迁移

状态迁移按策略表执行，不是所有任务都走满独立验收：

- 短路径：`pending → in_progress → worker_done → integrated → done`
- 独立验收：`pending → in_progress → worker_done → verifying → verified → integrated → done`

脚本拒绝非法跳转，并记录 `status_history`。角色约束如下：

- `in_progress`：`M1`
- `worker_done`：`worker`
- `verifying`、`verified`：`verifier`
- `integrated`、`done`：`M1`（短路径上 `worker_done → integrated` 也必须是 M1）
- `reopened`：`M1`，同时递增 `attempt`

状态显示中的 `done / GATE_FAIL` 只能视为历史字段异常；最终是否收口，以 `transition ... done --actor M1` 是否成功为准。

## 核心认知（三条铁规则）

1. **窗口号只有两套**：常驻 `M1`～`M10`；临时 `C1`、`C2`…（一轮最多 4 个）。禁止 `M11`、`CB1`、`窗口 8` 当窗号。对窗口说「我是 M4」或「我是 C1」。
2. **文件名 ≠ 窗号**：`CB2.json`、`"window": "CB2"` 只给合并/自检脚本认，写在 Registry 该窗章节的「产出文件」行。查收、打回、RECEIPT-LOG **只用窗号**（M4 / C1）。用户说「CB2 已完成」时，先在 Registry 解析成对应窗号再查收，回复改口用窗号。
3. **查收看命令过没过**：验收命令**只检查工人已经写在磁盘上的文件**，不负责生成那些文件。M1 按该窗验收命令**本窗重跑**。一行 `RESULT PASS` 只要是命令打出来的，就是全文。窗口**不会**自动把结果交给终端，也**不会**自动叫醒 M1。

同时生效：

- **空间**：M1 / 常驻 Mn / 临时 Cn 各守路径。**阶段**：斥候 / 主力 / 搜剿。
- **只有 M1** 能把模块标 `done`。子窗口搜剿通过 ≠ 全局完成。
- 用户说「所有窗口已完成」= **本轮派工**名单（其中的 M 与 C），不是历史上所有编号。
- 该窗未送达查收信号前，不得标 done（信号可以是口播、粘贴、已点名的关联会话、子代理回传）。
- **「亲自微修订」必须用户点名**。没点名就派对应 M 或 C。用户对 M1 说「开工 / 修一下 / 顺手」= 写 Registry + 开工话术，不等于本窗当主力。
- **网页预览仅 M1**。子窗口禁止打开网页预览。查收通过后能开则 M1 打开；打不开则跳过，不因此 fail。

### 环境：默认与加分

本 skill 不绑死某一款 Agent 软件，也不按软件名写死「永远加分」或「永远排除」。所有宿主都从**默认**开始。

加分只看**这一窗已经确认**的额外能力，不看产品说明书上「可能有」什么。窗间对话是否可见，各宿主不一样；窗间不可见也不等于不能加分——仍可能有别的额外能力（例如能把子代理结果收回本会话）。说明书上有子代理、本窗还没用上或未确认，**不是**加分。

| | **默认** | **加分** |
|--|----------|----------|
| 何时 | 所有环境的起点；未确认额外能力时全程按此 | 本窗已确认存在超出默认的额外能力（例如能引用其他对话原文，或能把子代理结果收回本会话） |
| 看见什么 | 不假设能看见其他会话的聊天原文 | 已确认的关联会话 / 子代理回传可作为**线索** |
| 协作 | 同一项目路径 + `docs/` + 用户把查收信号送到 M1 | 可少传话、少开窗 |
| 查收 | 本窗重跑该窗验收命令 | 同样必须本窗重跑。不能只凭「那边说完成了」标 done |
| 出句 | **不要说加分**，直接派工 | 前 3 轮必须点名「当前是加分状态」 |

**出句（硬性，只约束 M1）：** 只有判定为加分才准贴加分句。默认禁止说加分。不确定则前 3 轮内只问、禁止先报加分；未确认前按默认。前 3 轮没点名 → 本窗全程按默认。句式见 templates.md（加分句不是默认开场）。

**默认流程（所有宿主的起点）：** M1 派工写 Registry → **用户**开 M/C 窗并说「我是 {窗号}」→ 工人改文件并**自己在本窗终端跑验收命令** → **用户**对 M1 说「{窗号} 已完成，请查收」→ M1 **再跑同一条命令**。失败则 M1 打回，**用户**再到该工人窗说「按打回项继续」。没有「跑完自动交 M1」「失败自动重开」这一跳。

## 窗口角色（空间）

| 角色 | 职责 |
|------|------|
| **M1** | 大纲、Registry、查收、集成、RECEIPT-LOG；能开则打开网页预览。亲自改子模块必须用户点名且同意 |
| **M2～M10** | 常驻模块执行人：各守一块长期模块；环内斥候→主力→搜剿；禁止打开网页预览。已 `done` 的不必常开 |
| **C1、C2…** | 临时短任务（如一批词库）。只改本轮规定路径；查收通过后关掉。号码下一轮可复用。一轮最多 **4** 个 |
| **用户** | 开窗、传开工话术、把查收信号送到 M1；实测 |

历史项目里已有 `M11`～`M15` 且已 `done` 的：**保持原样，不要改名**。新开窗禁止再发 M11+。

## 阶段角色（斥候 / 主力 / 搜剿）

| 名称 | 允许 | 禁止 |
|------|------|------|
| **斥候** | 只读调查、列路径与可疑点、建议最小范围 | 改代码、声称已修好、改 Registry 为 done |
| **主力** | 最小改动实现；交计划、diff、本回合终端原文 | 越界；自己标 done；打开网页预览；只交 PASS 摘要；指望「脚本替我生成交付文件」 |
| **搜剿** | 只验收挑刺；施工窗没跑终端则 fail；M1 以本窗复跑为准 | 凭感觉放行；顺手改子模块；因没有子窗 stdout 打回 |

环内：斥候→主力→搜剿；搜剿 fail 则主力↔搜剿；达 4 次硬停写升级报告。完工话术：「{窗号} 已完成，请主导窗口查收。」

## 编号

| 字段 | 规则 |
|------|------|
| **常驻窗** | 仅 `M1`～`M10`。禁止 M11+、F2 |
| **临时窗** | 仅 `C` + 数字。一轮最多 4 个；done 后关闭，号码可复用。禁止 `CB1` 当窗号 |
| **产出文件** | 如 `data/words-batch/CB2.json`，与窗号可以不同；Registry 必须写清「窗号 C1 ↔ 文件 CB2.json」 |
| **章节标题** | `## M4 — 背单词` 或 `## C1 — 词库批次` |
| **对工人说** | `我是 M4` / `我是 C1` |
| **对 M1 说** | `M4 已完成，请查收` / `C1 已完成，请查收` |

## 本轮派工

```markdown
## 本轮派工
- 本轮窗口：M4、C1、C2
- 本阶段不使用：M6、M7
- 临时窗上限：本轮 2 个（最多 4）
```

无「本阶段不使用」可写「无」。未列入本轮的编号不得写成缺失或 fail。

## 自测证据

每个本轮窗口必须有**可原样执行的验收命令**（检查已有文件/能否编译）。禁止只写「自测通过」。必须真跑终端。验收命令**不生成**业务文件。

| 谁 | 标准 |
|----|------|
| **工人窗** | 本回合调用终端；回复含命令 + 输出。一行 PASS 且命令本就一行 → 就是全文，可以请查收 |
| **M1 查收** | 本窗重跑该窗验收命令。用户可贴原文，但不是必须。复跑通过即过 |

## 查收（只此一份）

1. 解析点名窗号（别名/文件名先换成 M 或 C）。所有窗口 = **本轮派工**名单。
2. 只核这些窗的路径、交付物、验收命令。
3. 磁盘：产出是否在规定路径。
4. 本窗重跑验收命令。通过 = **内容通过**。关联会话只当线索。
5. 本轮名单都通过 → 标 done、记 RECEIPT-LOG、做合并；能开则打开网页预览。临时 C 窗 done 后视为本轮关闭。
6. 命令失败 → **fail**，打回该窗（由用户再进该窗，不会自动重开）。
7. 本轮还有窗没交 → **整轮未齐**。

禁止：仅凭口头完成、磁盘有文件、或「我看到其他对话」标 done。

**亲自微修订**：未经点名且未经同意，禁止改子模块实现。未获同意则派 M 或 C，本窗只交 Registry 与开工话术。

## 长线治理：循环渐进 + 卡点上限 4

每一环：唯一目标、成功标准、证据（diff + 本回合终端原文）、存档点。同一卡点最多 4 次；第 4 次仍 fail → 硬停，写 `docs/BLOCKERS/`，点名下一步 {窗号} 的【斥候|主力|搜剿】或 M1 / 用户。

## 项目文档

M1 创建：`docs/MODULE-REGISTRY.md`、`docs/RECEIPT-LOG.md`、`docs/FIX-PLAN.md`；可选 `docs/BLOCKERS/`、`docs/TOOL-PATHS.md`。格式见 templates.md。

状态机：`pending` → `in_progress` → `review` → `done` | `blocked`。`done` 仅 M1 可标。

## 依赖、并行、集成

```
M1 定标准 → 常驻骨架模块先过查收 → 其余常驻 M 可并行；临时 C 可与常驻并行（各守文件） → M1 集成
```

v0.25：若本窗已确认能收回子代理结果，M1 可用宿主子代理加速工人活，但仍按上表守路径；未确认则仍由用户开窗。

| 原则 | 说明 |
|------|------|
| 同一文件只让一个窗主改 | 边界写死路径 |
| Phase 2 修复 | 复用已有 M2～M10 或开临时 C，不新建 F 编号、不发 M11+ |
| 修 Bug | 可叠加 `/bugfix`；仍受卡点 4 次约束 |
| 配置冲突 | M1 合并 |

## 反模式

- 开 M11+ 或把 CB1 当窗号
- 一轮临时 C 超过 4 个，或临时窗当长期部门
- 以为工人跑完验收会自动交给 M1，或 fail 会自动打回重做
- 把验收命令当成「生成文件的脚本」
- 未确认加分却说「当前是加分状态」，或凭「产品有子代理」自行升为加分
- 未点名加分却用「已看到其他对话」标 done
- 把加分问句写进 Mn/C 开工话术
- 整份覆盖 `manifest.json`，或把 `transition` 改过的 manifest 漏报当成必须打回的业务文件
- 让工人申报 `verify-report.json`，或让 C1 改 `worker-report.json` 来过 G2
- 子窗口自行标 done；没跑终端只编 PASS；子窗口打开网页预览
- M1 未经点名、未经同意就亲自改子模块
