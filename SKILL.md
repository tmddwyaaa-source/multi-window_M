---
name: multi-window_M-0.30
description: >-
  多窗口分工：常驻仅 M1～M10；短期任务用临时窗 C1、C2（一轮最多 4 个）。
  禁止 M11+、禁止 CB1 当窗号。json 文件名 CB2 只给脚本认。
  开局自报三套挡位；未确认禁止报加分或走协作挡 A。
  hook_supervision 为真时收口必须有宿主 stop 的 start/end 对，手动 audit-round 不能替代。
  子代理必须绑定 task_id/window/allowed_paths/run_id。
  状态表只由 taskctl status --markdown 从 .task/ 渲染；派工用 brief，接班用 handoff。
  默认不说加分；M1 唯一收口；查收以本窗重跑为准。
  规则只描述当前行为；门禁细节见 references/task-gate.md。
  斥候→主力→搜剿；卡点最多 4 次。
  在用户提到多窗口、M1、C1、查收、模块注册表、斥候/主力/搜剿时使用。
version: "0.30"
disable-model-invocation: true
---

# Multi-Window_M-0.30（常驻 M1～M10，临时 Cn）

本文件夹名带版本号，是升级系列的隔离副本；`version` 字段也是 0.30。调用：`/multi-window_M-0.30`。历史版本说明见 [CHANGELOG.md](CHANGELOG.md)，不得当作当前规则。

复制即用话术见 [templates.md](templates.md)。G0～G3、manifest schema、`transition` 表见 [references/task-gate.md](references/task-gate.md)。宿主 Hook 接线只允许写在 [references/hooks.md](references/hooks.md)。

## 本版唯一问题与成功标准

**问题：** 低挡假装高挡，或声称有 hook 监督却没有宿主 stop 日志，仍把任务标完成。

**成功标准：**

- 开局自报三套挡位（能力 / 协作 / 风险）。能力未确认一律默认；协作挡 A 仅限已确认加分。
- `round.json` 可写 `gears`（`capability=default|bonus`，`collaboration=P|A`，`capability_confirmed`）。未确认却写 bonus 或 A → `GEAR_VIOLATION`，停止收口。
- `hook_supervision=true` 时，`audit-round` 收口必须看到宿主 stop（`cursor-stop` / `codex-stop` / `zcode-stop`）的完整 start/end 对。缺日志或只有 `manual` → `HOOK_EVIDENCE_MISSING`。手动 `audit-round` 成功不能替代 hook 证据。
- 子代理写入 `subagents`（`task_id` / `window` / `allowed_paths` / `run_id`）。同文件并发、重复 run_id、工人兼 verifier、越权路径 → `PARALLEL_FAIL`。
- Hook 仍遵守三不：不改状态、不派工、不标 done。加分 / A 挡不放松验收：漏跑独立验收仍 `FULL_GATE_FAIL`。

0.29 的 `source_refs`、0.28 的状态视图、0.27 的 `brief` / `handoff`、0.26 的 `POLICY_CONFLICT` 仍是当前规则。本版不改窗号、状态机转移表或目录结构。

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

### 环境：三套挡位（开局自报）

本 skill 不绑死某一款 Agent 软件。三套挡位正交，只影响协作方式，**不影响验收纪律**（本窗重跑、`transition` 收口、M1 唯一收口在任何挡位不变）。不确定一律落低挡。

| 挡位 | 取值 | 判定 |
|------|------|------|
| **能力** | 默认 / 加分（bonus） | 本窗已确认的额外能力；未确认 = 默认。前 3 轮点名加分，不确定只问不报 |
| **协作** | P 人路由多窗 / A M1 子代理半自动 | P 是所有宿主起点且永久保留；A 仅限 `capability=bonus` 且 `capability_confirmed=true` |
| **风险** | 短路径 / 独立验收 | 唯一策略表：low 且 attempt<2 走短路径，其余独立验收 |

能力确认测试命令按宿主写在 `references/hooks.md`。新窗口跑一次、本窗实际收到结果才算确认。说明书上有子代理、本窗还没用上，**不是**加分，也**不能**把协作挡写成 A。

`round.json` 用 `gears` 记录能力挡与协作挡；风险挡仍由各任务 `risk` / `attempt` / `verification_required` 决定。未写 `gears` = 默认 + P。未确认却写 bonus 或 A → `GEAR_VIOLATION`。

加分只看**这一窗已经确认**的额外能力。窗间对话是否可见，各宿主不一样；窗间不可见也不等于不能加分——仍可能有别的额外能力（例如能把子代理结果收回本会话）。

| | **默认（P）** | **加分（可升 A）** |
|--|----------|------|
| 何时 | 所有环境的起点；未确认额外能力时全程按此 | 本窗已确认存在超出默认的额外能力 |
| 看见什么 | 不假设能看见其他会话的聊天原文 | 已确认的关联会话 / 子代理回传可作为**线索** |
| 协作 | 同一项目路径 + `docs/` + 用户把查收信号送到 M1 | 可少传话、少开窗；子代理必须写入 `subagents` 绑定 |
| 查收 | 本窗重跑该窗验收命令 | 同样必须本窗重跑。不能只凭「那边说完成了」标 done |
| 出句 | **不要说加分**，直接派工 | 前 3 轮必须点名「当前是加分状态」并自报三套挡位 |

**出句（硬性，只约束 M1）：** 开局先报本窗挡位。只有判定为加分才准贴加分句。默认禁止说加分。不确定则前 3 轮内只问、禁止先报加分；未确认前按默认。前 3 轮没点名 → 本窗全程按默认。句式见 templates.md（加分句不是默认开场）。

**默认流程（所有宿主的起点）：** M1 派工写 Registry 与 manifest → 跑 `brief` 把生成物交给工人 → **用户**开 M/C 窗并说「我是 {窗号}」→ 工人按简报改文件并**自己在本窗终端跑验收命令** → **用户**对 M1 说「{窗号} 已完成，请查收」→ M1 **再跑同一条命令**。失败则 M1 打回，**用户**再到该工人窗说「按打回项继续」。没有「跑完自动交 M1」「失败自动重开」这一跳。新 M1 先跑 `handoff`，不要凭聊天记忆接班。

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

## 任务控制（当前规则）

没有 `.task/` 的项目仍按上面的多窗口流程工作。启用 `.task/` 后：

- **唯一策略实现**是 `scripts/taskctl.py` 的 `verification_policy()`。Gate、`audit-round`、`transition`、M1 收口禁止各自解释。
- **任务状态**写在 `.task/TASK-xxx/manifest.json`。**窗口状态**写在 `.task/round.json` 的 `window_status`。`taskctl status` 可在窗口行旁标注对应任务终态，**不改写** `window_status`。应写成 `TASK-001: verified`、`M2: worker_done`。
- 低风险且 `attempt < 2` 且未设 `verification_required`：短路径 `worker_done → integrated`（`--actor M1`）→ `done`。其余：独立验收 `worker_done → verifying → verified → integrated → done`。同一时刻只允许走其中一条。
- 工人只能交 `worker_done`。需要独立验收时，仅 verifier 可交 `verifying` / `verified`。只有 M1 能 `integrated` / `done`。直接编辑 manifest 的状态不算有效收口。
- 规则冲突（无法唯一决定收口路径，或 manifest 自相矛盾）→ 输出 `POLICY_CONFLICT` 并停止。
- Full Gate 会重跑可识别的验收命令、核 evidence 路径、有 Git 时核工作区。`manifest.json` / `rerun.json` / `verify-report.json` 不必列入工人 `changed_files`。
- `--root` 必须放在子命令前面。常用命令与 schema 见 `references/task-gate.md`。
- 一键自检（不依赖写死的本机路径）：`py -3 scripts/taskctl.py selftest`。
- **派工简报：** `py -3 scripts/taskctl.py --root <项目根> brief TASK-xxx --role worker|scout|verifier`。M1 把终端输出原文贴给该窗或子代理，不要手写转述代替生成物。
- **接班简报：** `py -3 scripts/taskctl.py --root <项目根> handoff`。新 M1 先跑这一条再动手。不存在的 task 或非法 `--role` 会被拒绝。
- **状态视图：** `py -3 scripts/taskctl.py --root <项目根> status --markdown --write`。写入 `docs/TASK-STATUS.md`。禁止手改该文件；不要在 Registry / RECEIPT-LOG 里另写一套 pending/done 当权威。
- **需求覆盖：** 用户每条新需求写入 `source_refs`（或 round 的 `source_requirements`）并映射到带 `verify`/`verify_cmd` 的 R 项。缺映射 → `REQUIREMENT_COVERAGE_FAIL`，停止收口。禁止只改聊天话术。
- **挡位：** `round.json` 的 `gears` 记录能力挡与协作挡。未写 = 默认 + P。未确认的 bonus / A → `GEAR_VIOLATION`。
- **Hook 证据：** `hook_supervision=true` 时，收口必须有宿主 stop 的 start/end 对。缺日志或只有 manual → `HOOK_EVIDENCE_MISSING`。无 hook 宿主不要打开 `hook_supervision`，以免卡死最低挡。
- **子代理绑定：** 派出的子代理写入 `subagents`（`task_id` / `window` / `allowed_paths` / `run_id`）。同文件并发、重复、工人兼 verifier、越权 → `PARALLEL_FAIL`。

旧项目升级：用**已安装的本技能脚本**执行 `migrate-project --destination scripts/taskctl.py --force`，再跑门禁。

## Hook 边界

Hook 只是触发器，不是验收结论。有 `.task/` 时 `hook-audit` 写 `.task/hook-runs.jsonl`（`start`/`end` 成对，同一 `run_id`，最多 100 行，应 gitignore）。没有 `.task/` 时静默跳过。

`round.json` 的 `hook_supervision` 默认为 false。为 true 时，M1 跑 `audit-round` 收口必须看到 `source` 为 `cursor-stop` / `codex-stop` / `zcode-stop` 的完整一对；`--source manual` 或只跑 `audit-round` **不算** hook 证据。未声明监督时，缺 jsonl 不卡死最低挡，但也不得把缺日志当成完成证据。

Hook **三不**：不改状态、不派工、不标 done。失败只记账。即使 Hook 输出成功，也不能替代 M1 本窗重新运行 Full Gate。

宿主差异（事件名、适配器、能力确认测试命令）只写在 `references/hooks.md`。本文件与 `taskctl.py` 不按宿主名分支行为。

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
5. 启用 `.task/` 时：按策略表 `transition` 收口；`audit-round` 出现 `BASIC_GATE_PASS` + `FULL_GATE_PASS` + `ROUND_READY_TO_CLOSE` 后，M1 才能逐项 `done`。出现 `POLICY_CONFLICT`、`REQUIREMENT_COVERAGE_FAIL`、`GEAR_VIOLATION`、`HOOK_EVIDENCE_MISSING` 或 `PARALLEL_FAIL` 则停止收口，写 BLOCKERS。
6. 本轮名单都通过 → 标 done、记 RECEIPT-LOG、做合并；能开则打开网页预览。临时 C 窗 done 后视为本轮关闭。
7. 命令失败 → **fail**，打回该窗（由用户再进该窗，不会自动重开）。
8. 本轮还有窗没交 → **整轮未齐**。

禁止：仅凭口头完成、磁盘有文件、或「我看到其他对话」标 done。

**亲自微修订**：未经点名且未经同意，禁止改子模块实现。未获同意则派 M 或 C，本窗只交 Registry 与开工话术。

## 长线治理：循环渐进 + 卡点上限 4

每一环：唯一目标、成功标准、证据（diff + 本回合终端原文）、存档点。同一卡点最多 4 次；第 4 次仍 fail → 硬停，写 `docs/BLOCKERS/`，点名下一步 {窗号} 的【斥候|主力|搜剿】或 M1 / 用户。所有任务都必须过最低门禁。规模小不等于免检。

## 项目文档

M1 创建：`docs/MODULE-REGISTRY.md`（路径、交付物、验收命令）、`docs/RECEIPT-LOG.md`（查收叙事）、`docs/FIX-PLAN.md`；可选 `docs/BLOCKERS/`、`docs/TOOL-PATHS.md`。启用 `.task/` 后还必须生成 `docs/TASK-STATUS.md`：

```text
py -3 scripts/taskctl.py --root <项目根> status --markdown --write
```

**状态单一来源是 `.task/`。** `TASK-STATUS.md` 只是视图，禁止手改，只由上面的命令再生。Registry 总览表不要再手写状态列；RECEIPT-LOG 可以记本窗重跑原文，但 **结论 done / pending 以生成表和 `transition` 为准**。手写 Registry `done` 不能代替 `transition ... done`。

任务是否完成以 manifest 的 `transition` 为准。窗口状态仍在 `round.json` 的 `window_status`，不要把 `C1 done` 写进 round 冒充任务完成。

## 依赖、并行、集成

```
M1 定标准 → 常驻骨架模块先过查收 → 其余常驻 M 可并行；临时 C 可与常驻并行（各守文件） → M1 集成
```

并行只提高效率，不是新工作流：

- 只用**当前宿主已经提供**的多代理 / 子代理。宿主没有就不要强用，不要发明新窗口角色或新编号（不新增 F、Scout 窗、M11+）。
- 默认流程仍是用户开 M/C 窗并传查收。子代理不是窗号；活必须落在 Registry 已有 M/C 任务和 `allowed_paths` 上，并写入 `subagents`（`task_id` / `window` / `allowed_paths` / `run_id`）。
- 同一文件仍只让一个任务主改。不能用同一个子代理既当工人又当独立验收。
- 子代理回传说完成，只当查收**信号/线索**。M1 仍须本窗重跑，仍须 `transition` 收口。
- 未确认本窗有额外能力时，不要少开窗、少等用户传话，也不要说加分，协作挡保持 P。

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
- 引用 CHANGELOG 里的旧版块当当前规则
- 整份覆盖 `manifest.json`，或把 `transition` 改过的 manifest 漏报当成必须打回的业务文件
- 让工人申报 `verify-report.json`，或让 C1 改 `worker-report.json` 来过 G2
- 子窗口自行标 done；没跑终端只编 PASS；子窗口打开网页预览
- M1 未经点名、未经同意就亲自改子模块
- 低风险短路径与独立验收两条路同时走，或忽略 `POLICY_CONFLICT` 继续收口
- 派工时手写转述代替 `brief` 生成物，或新 M1 不跑 `handoff` 凭记忆接班
- 手改 `docs/TASK-STATUS.md`，或用 Registry / RECEIPT-LOG 的 pending/done 覆盖 `.task/`
- 用户口头加需求却不写 `source_refs` / `source_requirements`，或映射空的 `maps_to` 仍标完成
- 未确认却自报加分 / 协作挡 A，或 `gears.capability=bonus` 但 `capability_confirmed` 为假
- `hook_supervision=true` 却用手动 `audit-round` / `--source manual` 冒充 hook 证据
- 派出子代理却不写 `subagents`，或同文件并发 / 工人兼 verifier / 越权路径仍收口
- 加分或 A 挡漏跑独立验收，仍把任务标完成
