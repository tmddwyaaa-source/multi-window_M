# Multi-Window_M 模板

常驻窗：`M1`～`M10`。临时窗：`C1`、`C2`…（一轮最多 4）。`CB2.json` 等只当产出文件名。

把下面 `{窗号}` 换成 `M4` 或 `C1`。

## MODULE-REGISTRY.md

```markdown
# 模块注册表（Module Registry）

> **主导窗口 M1** 维护路径与交付物。查收 / 打回只用窗号（M2～M10 或 C1…）。
> 常驻仅 M1～M10。临时 C 一轮最多 4 个，done 后关闭、号码可复用。
> 禁止新开 M11+，禁止 CB1 当窗号。
> **状态禁止手写。** 运行 `py -3 scripts/taskctl.py --root <项目根> status --markdown --write`，以 `docs/TASK-STATUS.md` 为准。

## 本轮派工
- 本轮窗口：M4、C1、C2
- 本阶段不使用：M6、M7
- 临时窗上限：2（最多 4）

## 总览

状态列不要在本表手改。生成表：`docs/TASK-STATUS.md`。

| 编号 | 模块名 | 产出文件 | 负责窗口 | 依赖 |
|------|--------|----------|----------|------|
| M1 | 主导 / 架构协调 | — | **本窗口** | — |
| M4 | 背单词 | — | M4 | M3 |
| C1 | 词库批次 | `data/words-batch/CB2.json` | C1 | M1 |

## C1 — 词库批次

**产出文件**：`data/words-batch/CB2.json`（脚本字段 `"window": "CB2"`，不是窗号）

**路径**：只写该 json。禁止改 backend/frontend/docs。

**交付物**：
- [ ] 60～90 词，按 CONTENT-SPEC 一-B

**验收**（检查已有文件，不生成文件；把 n= 换成该文件名）：
- 见项目 `data/CONTENT-SPEC.md` 中 `python -c` 段，`n='CB2'`

**环内角色**：斥候 → 主力 → 搜剿；同一卡点最多 4 次

**开工话术**：`我是 C1 窗口，请读 MODULE-REGISTRY.md 中【C1】章节。按斥候→主力→搜剿执行。只改规定路径。必须贴终端输出原文。不要打开网页预览。`

**状态**：见 `docs/TASK-STATUS.md`（禁止在本章手写 pending/done）
```

常驻章节标题：`## M4 — 背单词`。临时：`## C1 — 词库批次`。不要写成 `## CB2`。

## 开长线硬话术

```text
本任务按循环渐进执行：
- 每环只做一个目标，必须有成功标准、证据、存档点
- 环内角色：斥候 → 主力 → 搜剿；fail 则主力↔搜剿
- 同一卡点签名最多 4 次；第 4 次仍 fail 必须硬停并写卡点升级报告
- 自测必须贴本回合终端原文；不要打开网页预览
- 常驻只称 M2～M10，临时只称 C1、C2…；不要用 CB1 / M11 当窗口号
- 工人跑完验收不会自动交给 M1；须用户传「{窗号} 已完成，请查收」
```

## M1 开场

默认：不要说加分，直接派工。加分句不是开场模板。

### 仅加分时使用（本窗已确认额外能力，前 3 轮）

```text
当前是加分状态：本窗已确认有超出默认的额外能力（作线索，不能代替本窗重跑验收命令）。
```

禁止在未确认时贴上面这句。说明书上「可能有子代理」不够。

### 不确定时只问（禁止先报加分）

```text
当前环境是否已能关联其他对话，或已能把子代理结果收回本会话？能则本窗为加分（须点名）；不能或未确认则按默认，不要说加分。
```

## M1 接班

新 M1（或同一人换对话）先跑生成物，不要凭上一窗聊天记忆接班：

```text
py -3 scripts/taskctl.py --root <项目根> handoff
py -3 scripts/taskctl.py --root <项目根> status --markdown --write
```

把 `handoff` 与 `docs/TASK-STATUS.md` 当接班状态源。然后再：

```text
/multi-window_M-0.29
我是 M1。已阅读 handoff 生成物。按未闭环项继续；查收仍须本窗重跑；M1 唯一收口。
不要报加分（除非本窗已确认并点名）。不要手写转述代替 brief。
```

## 派工：先 brief 再贴窗

M1 写好 manifest / round 后，不要手写开工长文。生成简报并**原样**贴给工人或子代理：

```text
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role worker
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role scout
py -3 scripts/taskctl.py --root <项目根> brief TASK-001 --role verifier
```

`--role` 只能是 `worker` / `scout` / `verifier`。不存在的 task 或非法 role 会被拒绝。`brief` 与 `handoff` 不改状态。

工人窗仍用下面的简版话术作身份句；任务细节以 brief 生成物为准。

## 子窗口简版话术

**开工：**

```text
我是 {窗号} 窗口，请读 {项目路径}/docs/MODULE-REGISTRY.md 中【{窗号}】章节。
若用户贴了 taskctl brief 生成物，以简报为准（allowed_paths、R 项、验收命令）。
按斥候 → 主力 → 搜剿执行；同一卡点最多 4 次。
只改该模块规定路径，勿覆盖其他模块文件。
必须用终端跑 Registry 验收命令，回复里贴命令 + 终端输出原文；没有原文不许说请查收。
不要打开网页预览。
不要整份覆盖 manifest.json；状态只用 transition。不要把加分问句写进本窗开工词。
```

**完工汇报（对 M1）：**

```text
{窗号} 已完成，请主导窗口查收。
```

不要说「CB2 已完成」。文件名可附一句「（产出 CB2.json）」但不替代窗号。

## 角色提示词（复制即用）

### 斥候

```text
/multi-window_M-0.29
我是 {窗号}。当前角色：斥候（只调查，禁止改任何文件）。
请读 {项目路径}/docs/MODULE-REGISTRY.md 中【{窗号}】章节。
主题：{一句话}

硬性规则：只读本模块规定路径；禁止写代码；禁止改 Registry 为 done。

必须交付：
## 斥候报告 — {窗号}
- 相关文件路径清单
- 关键现状/可疑点（文件:行号 或日志）
- 约束（依赖、接口、平台、权限）
- 建议的最小改动范围（不要写实现代码）
- 是否 blocked（缺什么信息）
```

### 主力

```text
/multi-window_M-0.29
我是 {窗号}。当前角色：主力（只实现，最小改动）。
请读 Registry 中【{窗号}】章节。依据斥候报告（若有则以下为准）：
---
{粘贴斥候报告}
---

硬性规则：
1) 只改该模块规定路径
2) 先给 3–7 步计划 + 成功标准，再动手
3) 结束必须交：关键 diff 说明 + Registry 验收命令的终端输出原文
4) 验收命令只检查已写好的文件，不要指望它替你生成交付物
5) 不要自己把 Registry 标成 done；不要打开网页预览
6) 修 Bug 时可叠加 /bugfix

交付后说：{窗号} 已完成，请主导窗口查收。
```

### 搜剿 — 子窗口自检

```text
/multi-window_M-0.29
我是 {窗号}。当前角色：搜剿（禁止修改实现代码）。
卡点签名：{现象 + 位置/测试}
本轮循环计数：{k}/4
主力报告与证据：
{粘贴}

本窗未跑终端 → fail。一行 RESULT PASS 且命令本来就只打一行 → 可 pass。

交付：
## 搜剿结论 — {窗号}
- 结果：pass / fail
- 证据：是否含本回合命令 + stdout 原文
- fail 打回项
- 若计数将达 4：硬停，改输出卡点升级报告
```

### M1 最终查收

```text
/multi-window_M-0.29
我是 M1。当前角色：搜剿（只验收，禁止顺手改子模块来“修完”）。
用户汇报：{窗号} 已完成，请查收。（或：所有窗口已完成 = 只核本轮派工名单）

严格执行：
1) 范围 = 点名的 M 或 C（CB2.json / 别名先解析成窗号），或本轮派工名单
2) 读磁盘：产出是否在规定路径
3) 本窗重跑该窗验收命令；一行 PASS 且复跑一致即可
4) 对照交付物清单
5) 内容通过 → RECEIPT-LOG；本轮名单都通过才标 done / 做合并；临时 C done 后关闭
6) 命令失败 → fail，打回该窗（等用户再进该窗，不要假装已自动重开）
7) 仅本窗已确认并点名加分时，关联会话 / 子代理回传可当线索；仍必须重跑命令。未确认则不要说加分。
禁止：仅凭口头或「我看到其他对话」标 done。结论用窗号，不用 CB2 当窗名。
```

## 卡点升级报告（docs/BLOCKERS/）

`docs/BLOCKERS/{窗号}-{短标题}.md`：

```markdown
## 卡点升级报告 — {窗号} / 环#{k}

### 卡点签名
- 现象：
- 主要位置/测试：
- 已循环次数：4/4

### 已尝试过的改法（按时间）
1. 改了什么 → 结果
2. …
3. …
4. …

### 当前最可能根因
- …

### 明确排除了什么
- …

### 需要的人类/M1 决策
- [ ] 换方案
- [ ] 补环境/密钥/数据
- [ ] 缩小范围 / 砍需求
- [ ] 指定其他窗口协助（M? 或 C?）

### 指定下一步（点名）
- 交给：{窗号} 的【斥候|主力|搜剿】或 M1
- 要检查的漏洞/检查项：
- 在此之前：本窗口禁止继续对同一卡点盲改
```

## 完成报告（子窗口）

```markdown
## 模块完成报告 — {窗号}

- 模块名称：
- 产出文件：（如 CB2.json，无则 —）
- 产出路径：
- 自测命令：
- 终端输出原文：
- 环内角色流：斥候 → 主力 → 搜剿
- 与 Registry 的差异：
- 遗留问题：
```

## RECEIPT-LOG 查收条目

```markdown
## {日期} 查收 — {窗号}

| 检查项 | 结果 |
|--------|------|
| 产出路径 | ✅ / ❌ |
| 本窗重跑验收 | ✅ / ❌ |
| 终端输出（含一行 RESULT PASS 且命令本就一行） | ✅ / ❌ |
| Registry 交付物 | ✅ / ❌ |

**结论**：本窗重跑结果写在这里；**是否 done 以 `docs/TASK-STATUS.md` 与 `transition` 为准**，不要在本文件手写一套与 `.task/` 不同的 pending/done。
**阻塞项**：…
```

标题只用 M4 / C1，不要用 CB2。

## FIX-PLAN Phase 2 头部

```markdown
# 修复方案（Phase 2）

> 复用 M2～M10，或开临时 C。不新增 F 编号，不发 M11+。

| 模块 | 本阶段 | 职责 |
|------|--------|------|
| M1 | ✅ | 查收、集成 |
| M{n} 或 C{n} | ✅ / ❌ 不用 | … |
```

## 工具路径约定（可选）

```
D:\gongju\{工具名}-{版本号}/
```

## README 导航块

```markdown
## 多窗口分工

| 文档 | 用途 |
|------|------|
| docs/MODULE-REGISTRY.md | 模块清单（路径/交付物；不要手写状态列） |
| docs/TASK-STATUS.md | 由 `status --markdown --write` 生成的状态表，禁止手改 |
| docs/RECEIPT-LOG.md | 查收叙事（结论以 TASK-STATUS / transition 为准） |
| docs/FIX-PLAN.md | Phase 2 修复分工 |
| docs/BLOCKERS/ | 卡点升级报告（达 4 次上限） |

**作战条令**：常驻 M1～M10；临时 C 一轮最多 4。禁止 M11+、禁止 CB 当窗号。查收以本窗重跑为准；工人不会自动交 M1。未确认额外能力不要说加分。网页预览仅 M1。可用宿主已有子代理加速，不新增角色窗。

## 任务控制层模板

### 任务目录

```text
.task/
├─ round.json
├─ TASK-001/
│  ├─ manifest.json
│  ├─ worker-report.json
│  ├─ verify-report.json
│  └─ evidence/
└─ taskctl.py
```

### manifest.json

```json
{
  "task_id": "TASK-001",
  "title": "一句话任务名",
  "owner": "M4",
  "track": "feature",
  "risk": "medium",
  "attempt": 0,
  "status": "pending",
  "status_history": [],
  "allowed_paths": ["src/", "tests/"],
  "source_refs": [
    {"id": "S1", "text": "用户原始需求一句话", "maps_to": ["R1"]}
  ],
  "requirements": [
    {"id": "R1", "text": "可观察的需求", "verify": "py -3 tests/test_example.py", "verify_cmd": "py -3 tests/test_example.py"}
  ]
}
```

### worker-report.json

```json
{
  "task_id": "TASK-001",
  "window": "M4",
  "status": "worker_done",
  "covered_requirements": ["R1"],
  "evidence": [
    {"requirement_id": "R1", "path": "tests/example.spec.ts", "note": "覆盖成功路径"}
  ],
  "changed_files": ["src/example.ts", "tests/example.spec.ts"],
  "tests": [{"command": "py -3 tests/test_example.py", "exit_code": 0}],
  "known_gaps": []
}
```

`changed_files` 只列工人改的业务文件（实现、测试、`worker-report.json`、evidence）。不要列入 `manifest.json` / `rerun.json` / `verify-report.json`。不要整份重写 `manifest.json`。C1 不要改工人的 `changed_files` 来「补报」验收报告。

### rerun.json（Full Gate 写入，不要手改当证据）

```json
{
  "task_id": "TASK-001",
  "at": "2026-09-05T16:00:00+00:00",
  "git": "ok",
  "diff_files": ["src/example.ts"],
  "commands": [{"command": "py -3 tests/test_example.py", "exit_code": 0}],
  "evidence_ok": true
}
```

### verify-report.json

```json
{
  "task_id": "TASK-001",
  "reviewer": "C1",
  "result": "pass",
  "checked_requirements": ["R1"],
  "missing": [],
  "notes": "逐条对照 manifest、diff 和证据"
}
```

当前规则：`reviewer` 必须是独立窗口，不能等于 `worker-report.window`；`changed_files` 必须全部落在 `manifest.json` 的 `allowed_paths` 内；`evidence[].path` 必须真实存在。Full Gate 会重跑 `verify_cmd` / 可执行的 `verify` / `tests[].command`，并写 `rerun.json`。**`manifest.json`、`rerun.json`、`verify-report.json` 未列入工人 `changed_files` 不算漏报。** 任务状态写在 manifest，窗口状态写在 round.json，不要混写。状态变更必须通过 `taskctl.py transition`，直接编辑 `status` 或整份覆盖 manifest 不算有效收口。策略冲突输出 `POLICY_CONFLICT`。细节见 `references/task-gate.md`。

### 本轮状态 round.json

```json
{
  "round_id": "ROUND-001",
  "expected_windows": ["M4", "C1"],
  "receipts": [],
  "window_status": {"M4": "pending", "C1": "pending"},
  "tasks": {"M4": ["TASK-001"], "C1": ["TASK-002"]},
  "source_requirements": [
    {"id": "S1", "text": "用户本轮原始需求"}
  ],
  "check_requested": false
}
```

用户发送「M4 已完成，请查收」后，M1 记录 receipt；所有本轮窗口都收到后，将 `check_requested` 设为 `true`，再运行 `taskctl.py audit-round`。脚本会检查 `window_status` 是否存在、覆盖全部窗口，并与 receipts 一致。用户中途加需求：先写入 `source_requirements` / 对应任务 `source_refs` 并补 R 项与 verify，禁止只改聊天。未映射 → `REQUIREMENT_COVERAGE_FAIL`。

### 开工补充话术

```text
本任务除原有 Registry 规则外，使用 .task/TASK-xxx/manifest.json 的 R 编号。
source_refs 必须覆盖用户原始需求；不要做 brief 里没有映射的额外需求。
只改 allowed_paths；结束时写 worker-report.json，逐条列出已覆盖的 R 编号、证据和已知遗漏。
若开工消息含 taskctl brief 生成物，以简报为准。
changed_files 只报你改的业务文件，不要报 manifest.json / rerun.json / verify-report.json。
worker_done 不等于 verified，不要自行标 done 或只口头说 PASS。
状态变更必须使用 taskctl.py transition，并填写正确 actor；不要直接编辑 manifest.status，不要整份覆盖 manifest.json。
加分问句只给 M1 开场用，不要写进本窗开工词。
```

### 独立验证话术

```text
当前角色：独立验证，只读，禁止修改实现代码。
读取 manifest.json、worker-report.json、当前 diff 和测试证据。
逐条检查所有 R 编号；有任何遗漏就输出 fail，并写 verify-report.json。
全部有证据且符合需求才输出 pass。
```

### M1 状态收口命令

短路径（low 且 attempt < 2，不要求独立验收）：

```text
py -3 scripts/taskctl.py transition TASK-001 in_progress --actor M1
py -3 scripts/taskctl.py transition TASK-001 worker_done --actor worker
py -3 scripts/taskctl.py transition TASK-001 integrated --actor M1
py -3 scripts/taskctl.py transition TASK-001 done --actor M1
```

独立验收路径（medium/high，或 attempt >= 2，或 verification_required）：

```text
py -3 scripts/taskctl.py transition TASK-001 verifying --actor verifier
py -3 scripts/taskctl.py transition TASK-001 verified --actor verifier
py -3 scripts/taskctl.py transition TASK-001 integrated --actor M1
py -3 scripts/taskctl.py transition TASK-001 done --actor M1
```

`verified`、`integrated`、`done` 每次都会重新运行 Full Gate；任意一步失败都不得继续。medium/high 或 `attempt >= 2` 禁止 `worker_done → integrated`。若人工验收发现问题，使用 `reopen TASK-001 --actor M1 --reason "..."`。

### Hook 触发证据

```text
结束一轮对话后看 .task/hook-runs.jsonl：同一 run_id 应有 phase=start 与 phase=end。
字段含 host、source、project_root、round_id、exit_code。最多 100 行。
该文件应在 .gitignore。手动 audit-round 不算 Hook 证据。
```

### 旧项目迁移

```text
使用已安装的本技能脚本执行。--root 必须放在子命令前面：
py -3 <本技能目录>\scripts\taskctl.py --root <项目根目录> migrate-project --destination scripts/taskctl.py --force
错误示例（会被拒绝）：
py -3 <本技能目录>\scripts\taskctl.py migrate-project --force --root <项目根目录>
.gitignore 增加：.task/hook-runs.jsonl
``` 
