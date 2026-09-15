# multi-window_M 历史版本

当前规则以 `SKILL.md` 的 `version` 字段为准。本文件只作历史说明，**不得当作当前规则引用**。

## 0.35-dsh — schema 契约、只读 status、关轮、不可重放命令（2026-09）

依据 `skill测试报告.md`（D:\8xgg\project 三轮真实交付，19 个任务）与 Codex 的《给 DSH：0.34 实测后的兼容性决策与实施顺序》。**0.34 目录保持不动**，本版是新建隔离副本。

### 解决的实际问题

| 问题 | 出处 | 处理 |
|---|---|---|
| `status` 会逐任务重跑 Gate（含 ~90s 回归命令），多任务时被 harness 120s 掐断 | 报告 §4.8 | **`status` 改为只读**：不执行任何 shell 命令，只显示上次真跑的记录（`GATE_PASS`/`GATE_FAIL`/`GATE_NOT_RUN`/`GATE_STALE`）。需要重算用显式 `--deep`。收口仍由 `gate --full` / `audit-round` 负责 |
| 没有关轮命令，第二轮开不了，只能手工删 `round.json` | 报告 §2.3 | **新增 `round-close`**：仅当整轮任务全部 `done` 且审计通过，才把 `round.json` **原子归档**到 `.task/rounds/`；未完成任务一律拒绝；绝不自动触发 |
| `tests[].command` 写占位符 / 说明文字 / 已删文件，Full Gate 逐字重放 → 永久 FAIL | 报告 §2.9、§4.3、§4.7、§4.15（**同一根因出现 3 次**） | **新增 `UNREPLAYABLE_COMMAND` 预检**：占位符、全角/中文说明、无运行器前缀、引用已声明却缺失的文件 → 收口前直接报明码并指出条目。**它是验收证据错误，不推进卡点计数** |
| verify-report 会在文件被改后继续被当证据用 | 报告 §2.22 | **新增 `STALE_REPORT`**：报告自称的 `changed_files` 之后又被改过 → 打**软提示**（不判 FAIL、不进卡点）。判据用**内容指纹**，不用 mtime |
| 两个宿主可能用各自旧的理解改写对方新写的数据 | Codex 决策 | **新增 `.task/` `schema_version`**：读到更高版本 → `UNSUPPORTED_SCHEMA`，**fail closed**：只允许查看，禁止改状态与收口；缺失/更低 → 走迁移兼容路径 |
| 文档暗示 `reassign --window` | 报告 §2.2 | **保留准确的 `--owner`**（Codex 决定：`window` 会混淆 worker 与 verifier）。`assign-verifier --window` 专指 verifier，两者不混用。修正文档与模板 |

### 新增/变更的子命令与字段

- 新子命令：`round-close`。`status` 新增 `--deep`。
- 新诊断码：`UNSUPPORTED_SCHEMA`、`UNREPLAYABLE_COMMAND`、`STALE_REPORT`。
- 新字段：`.task/*/manifest.json` 与 `.task/round.json` 顶层 `schema_version`；`rerun.json` 新增 `files`（内容指纹）与 `stale_report`。
- `KNOWN_COMMANDS` 补齐原先缺失的 `assign-verifier` / `sync-worker-route` / `packet`（影响 `--root` 位置校验）。

### 纳入的跨宿主共同原则（Codex 确认）

1. `.task/` schema、状态机、`owner`/`verifier`、`block_id`、三次卡点梯级与核心 Gate 是**跨宿主共同核心**。
2. DSH 的 `packet` / `read_budget` / 锚点诊断 / `dsh-runs.jsonl` / 宿主参数是**附加层**，不得改变共同字段含义或收口结论。
3. 新字段必须 optional 且有旧行为默认值。
4. Hook 只是账本和触发器：不改状态、不派工、不标 done。
5. **一任务只能有一个 `owner`**；多人协作拆子任务。
6. **M1 代验不算独立验收**：verifier 窗口缺席时任务保持未完成；只有派工前明确调整风险策略才可不要求 verifier。
7. 旧 manifest 缺 `block_attempts` 时回落任务级 `attempt`，标注 `LEGACY_UNSCOPED`，保守兼容**保留**。

### 测试

- 本分支 `selftest` 全绿：**15 套**（新增 `test_dsh_035.py`，32 项断言，覆盖每个改动的正例与负例）。
- Codex 版 `test_retry_ladder.py` 在本分支上**原样通过（5/5）** —— 状态机计数语义未被本轮改动触及。
- 关键正例（防误杀）：`node _tools/smoke.mjs`、`node --check src/app.js`、`py -3 -c "print(1)"`、`2>&1` / `> out.log` 重定向、**本机未安装的 `pytest`** 一律**不**报 `UNREPLAYABLE_COMMAND`。

## 0.34-dsh — DSH 分支起点（与 Codex 版合并后的 DSH 版）

本分支（`dsh-version`）以 Codex 版 0.34 为基线，合并 DSH 宿主专属能力，并**统一两支的命名**，使 `test_retry_ladder.py`（Codex 版）与本分支的 `test_block_staircase.py` **同时通过**。

### 新增（DSH 专属）
- **`packet` 就绪包**：`brief` 全部内容 + 读取预算 + 输出预算 + 归属确认；`--write` 落到 `.task/<task>/packet-<窗号>.md`。头部固定声明"**可再生成的派工副本、禁止手改、以 manifest/round 为准**"与"**读取预算是派工约束，不是安全权限边界**"。
- **`manifest.read_budget`**：`{path, anchor?, lines|full, why}`；`lines` 只接受 `N-M` 或 `full`，`anchor` 给了就不能为空，其它形状 Full Gate 拒绝。
- **`packet --check-anchors`**：只告警不阻塞的锚点检查，三类分开 —— `ANCHOR_FILE_NOT_FOUND`（文件不存在）/ `ANCHOR_NOT_FOUND`（真漂移）/ `ANCHOR_LOOSE`（仅格式变化）；粗匹配**跳过 Markdown 前导符号**取第一个有意义的词。**不进 Full Gate、不改状态**。
- **DSH 证据账本**：`.task/dsh-runs.jsonl`（独立 400 行额度）+ `dsh-hook-bridge.py` + `dsh-hooks.example.json`；`--host dsh`、`--session`、`--agent`、`--intent pair|record`；`STOP_HOOK_SOURCES` 含 `dsh-stop` / `dsh-subagent-end`。

### 统一命名（两支兼容）
| 概念 | 统一后的写法 | 兼容别名 |
|---|---|---|
| 换人命令 | `reassign --owner <窗号>` | （本分支原为 `attempt --owner`，保留） |
| 失败账 | `block_history`（长度=失败次数，不含换人） | `block_attempts.history` |
| 第 2 次 fail | 输出 `REASSIGN_REQUIRED`，`status=blocked`，`reassign_required=true`，写 `docs/BLOCKERS/<task>-<block>.md` | — |
| 第 3 次 fail | 输出 `HARD_STOP`，`hard_stop=true`，退出码非 0 | — |
| 改根因无证据 | `BLOCK_ID_CHANGE_REQUIRES_EVIDENCE` | — |
| 阶梯上限 | `MAX_BLOCK_ATTEMPTS = 3`、`REASSIGN_ON_SAME_BLOCK_FAILURE = 2` | — |

**关键不变**：换人**不写入** `block_history`（那是失败账，长度必须等于失败次数）；交接由 `assignment_history`（不可覆盖）承载。

### 测试
- 本分支：`selftest` 全绿（含 `test_packet.py` 16 项、`test_block_staircase.py` 18 项、`test_verifier_assignments.py` 18 项、`test_dsh_bridge.py` 16 项）。
- Codex 版 `test_retry_ladder.py` 在本分支上**原样通过**（5/5）。

## 0.34 — 一任务两职责：worker + 独立 verifier（2026-09）

修复"实现窗与验收窗无法同时正式派工"的缺口（与 Codex 版 v0.34 语义对齐）。语义版本升到 0.34；目录名仍是 `multi-window-m-034`（语义版本不带点，隔离副本目录名不随语义版本改名）。

- **两条路由分开存**：`round.tasks` 只表达**实现路由**（必须等于 `manifest.owner`）；新增 `round.verifier_assignments` 表达**独立验收路由**。一个任务同时有一个 owner 和一个 verifier，互不冒充。
- 新增门禁 token：`VERIFIER_ASSIGNMENT_MISSING`（需要独立验收却没派 verifier）、`VERIFIER_ASSIGNMENT_CONFLICT`（verifier 非本轮窗 / 等于 owner 或 worker / 短路径多派 / `verify-report.reviewer` 不等于被指派 verifier）、`WORKER_ASSIGNMENT_CONFLICT`（实现路由 ≠ owner）。
- **reviewer 必须等于被指派的 verifier**——不再"只要不是 worker 就放行"（防冒名验收）。
- 新增命令 `assign-verifier TASK --window C2`（只写验收路由，**不动** `owner` / `attempt` / `block_id` / `assignment_history`；不是 `reassign`）与 `sync-worker-route TASK`（修复旧项目错误主路由；验收开始后拒绝改动）。
- `brief --role verifier` 现在**输出被指派的 verifier 窗号 + 实现 owner/窗号**；未被指派、窗号给错、任务其实走短路径 → **拒绝生成**（`BRIEF_FAIL`），不再产生含糊指令。`brief` 新增 `--window` 断言。
- `round-init` 新增 `--verifier TASK=WINDOW`；`receipt --role verifier`（必须在 `verifier_assignments` 内），回执写入 `receipts_by_role`，状态表把 C2 标为 `(verifier)`。
- 新增 `scripts/test_verifier_assignments.py`（18 项正负向）。
- **DSH 方向 A/B（本宿主专属，非 Codex 方案）**：新增 `taskctl.py packet` 就绪包 —— 简报全部内容 + **读取预算** + **输出预算** + 归属确认，`--write` 落到 `.task/<task>/packet-<窗号>.md`，窗口读这一份即可开工，不必翻 Registry、不必整篇读大件；`manifest.read_budget`（`N-M` 或 `full`，可选 `anchor` 锚点；其它形状门禁直接拒）渲染进 `brief`/`packet`。新增 `scripts/test_packet.py`（14 项正负向）。
  就绪包头部固定声明：**可再生成的派工副本、禁止手改、以 manifest/round 为准**；**读取预算是派工约束，不是安全权限边界**。
  `packet --check-anchors`：**只告警不阻塞**的锚点存在性检查，**不进 Full Gate、不改状态**；三类结果分开（`ANCHOR_FILE_NOT_FOUND` 文件不存在 / `ANCHOR_NOT_FOUND` 锚点漂移 / `ANCHOR_LOOSE` 仅格式变化），粗匹配**跳过 Markdown 前导符号**再取第一个有意义的词。新增用例至 16 项。
  理由：用户只说一句「你是 Mn，请完成指示任务」，**开窗与传话已经是最省的状态**；剩下的成本在执行端（反复读大件）与 M1 侧（步数），读写量才是可降的部分。
- **未变**：M1 唯一收口、M/C 窗号与临时 C 上限、卡点三级阶梯、Hook 三不、子代理不得充当正式 worker/verifier/owner 的边界。

## 0.33 — 卡点阶梯 + 根因绑定 + 性能纪律（2026-09）

三方对齐（用户 / DSH / Codex）后落地。**稳定性规则不动**：Full Gate、独立验收、状态机、Hook 证据、`.task/` 记录一条都不削。

- **卡点上限 4 → 3**，写成三级阶梯：第 1 次 fail 复用原执行者并复述打回项；第 2 次 fail **换真正不同的负责窗口并更新 `owner`**；第 3 次 fail 硬停写 `BLOCKERS`。
- **计数绑定根因**：`manifest.block_attempts`（`block_id` + `attempts` + `history`）。同一根因**不能靠改名重置**（改 `block_id` 不给 `--new-root` 直接拒绝）；`--new-root` 必须带证据。
- **交接历史不可覆盖**：`manifest.assignment_history` 独立存放，改根因也不得抹掉。换人不能只在聊天里说。
- **不得伪造换人**：宿主无合格替换者时写 `BLOCKERS` 的 `NO_ELIGIBLE_REPLACEMENT_WORKER`，交 M1 / 用户决定。
- **两轴分离确认**：`attempt`（任务级，只驱动验收策略）与 `block_attempts`（卡点级，只驱动阶梯）互不混用。
- 新增 `taskctl.py attempt` 子命令（`--block-id` / `--reason` / `--owner` / `--role` / `--new-root`，actor 为 worker|verifier）；`reopen` 必须带 `--block-id`。`status` 增加"卡点"列，`brief` 增加卡点预算行。
- **性能纪律（压效率，不压验收）**：回执预算（上限只管叙述，命令输出原文必须完整保留）、有界读取（>100 行或 >4KB 只允许区间/片段）、M1 状态驱动输出（异常仍须立即处理）、斥候按需触发（判据是"知不知道文件在哪"）。
- **派工纪律**：派工内容 = 原样贴 `brief` 生成物，M1 不另写说明。
- **宿主适配（DSH）**：固定 `capability=default` + `collaboration=P`、不打开 `hook_supervision`；子代理**不得**担任正式工人 / 替换负责人 / 验收者；派子代理必须自带**任务胶囊**（见 `templates.md`）。
- 新增 `scripts/test_block_staircase.py`（14 项正负向），并同步 `brief` / `status` / `task-gate.md` / `templates.md`。

## 0.32 立即补丁 — 宿主标识合法化 + dsh 证据账本（不另占版本）

- 技能名与文件夹统一为 `multi-window-m-034`。原 `multi-window-m-0-32` / `multi-window_M-0.32`
  在宿主里会被当**标识符**解析（下划线、点号不是合法技能名字符），调用名因此对不上 `name` 字段。
  调用改为 `/multi-window-m-034`。语义版本仍是 `0.32`，规则与状态机不变。
- 新增第二本宿主证据账本 `.task/dsh-runs.jsonl`（dsh 专用，上限 400 行）。Cursor / Codex / Zcode
  仍写 `.task/hook-runs.jsonl`（上限 100 行）。`audit-round` 读两本。
- `hook-audit` 新增 `--host dsh`、`--session`、`--agent`、`--intent pair|record`，并接受
  `dsh-stop` / `dsh-subagent-end` / `dsh-session-start` / `dsh-prompt` / `dsh-pre-tool` /
  `dsh-post-tool` 六个 source。`STOP_HOOK_SOURCES` 增加前两个。
- 新增 `scripts/dsh-hook-bridge.py`：把 dsh hooks 桥的 stdin 载荷翻成一次 `hook-audit` 调用。
  dsh 的 hook 事件是一次性的，所以桥接**一次调用写完整 start/end 对**，`session_id` 即 `run_id`。
  桥接恒退出码 0——dsh 的 `Stop` 是能阻塞的串行监听器且无连续阻塞上限，非 0 会被读成阻塞并自锁。
- 新增 `scripts/dsh-hooks.example.json`（Claude Code 方言接线样例）与 `scripts/test_dsh_bridge.py`
  （16 项正负向检查）。dsh 从「无 hook 宿主」段落里移出，改为已接线宿主；剩余那段改名
  「无 hook 能力的宿主」，只指产品本身没有 hook 触发点的环境。
- 不变项：`verification_policy()`、`transition` 表、失败 token、Hook 三不、`disable-model-invocation`。

## 0.32 — 窗口身份收口（2026-09-08）

- SKILL 增加唯一权威节「窗口身份（窗号）」；铁规则 1～2、对窗说话、反模式、templates 只指向该节。
- 合法窗号仍是 `M1`～`M10` 与 `C`+数字；词法以 `WINDOW_RE` / `valid_window()` 为准，文档是镜像。
- 不再把 `CBn` 当一种窗号来教。产出用普通文件名。不新开窗类别。
- `round-init` 仍拒绝非法窗号、重复窗号、一轮超过 4 个临时 C。不新增失败 token。不改状态机。

## 0.31 — 迁移治理（2026-09-07）

- `migrate-project` 覆盖前先备份到 `.task/migrate-backups/`，并写 `docs/MIGRATE-REPORT.md`。
- `.task/skill-lock.json` 记录 `skill_version` / `taskctl_version` / `migrated_at`。
- 迁完必须 `migrate-project --check`（基本门禁、Full Gate 可跑、Hook 不改状态、负向仍拒绝）。通过才 `MIGRATE_READY`。
- 已有目标且无 `--force`、或迁到正在运行的自身 → `MIGRATE_FAIL`。

## 0.30 — 挡位正式化 + 证据收紧（2026-09-07）

- 开局自报三套挡位。`round.json` 增加 `gears` / `hook_supervision` / `subagents`。
- 未确认却写 bonus 或协作挡 A → `GEAR_VIOLATION`。加分不放松验收。
- `hook_supervision=true` 收口必须有宿主 stop 的 start/end 对；手动 `audit-round` / `--source manual` 不能替代 → `HOOK_EVIDENCE_MISSING`。
- 子代理必须绑定 `task_id` / `window` / `allowed_paths` / `run_id`。同文件并发、重复、工人兼 verifier、越权 → `PARALLEL_FAIL`。
- Hook 仍三不：不改状态、不派工、不标 done。

## 0.29 — 需求覆盖（2026-09-07）

- manifest 增加 `source_refs`（原始需求 → R 项）。每条 R 必须被映射；`maps_to` 不能为空。
- `round.json` 可选 `source_requirements`。`audit-round` / Full Gate 发现未映射需求输出 `REQUIREMENT_COVERAGE_FAIL` 并停止收口。
- 用户新增需求必须走「来源记录 → R 项 → 验收命令」，禁止只改聊天话术。

## 0.28 — 文档变视图（2026-09-07）

- `taskctl.py status --markdown` 从 `.task/` 渲染窗口/任务状态表与 RECEIPT 摘要。
- `--write` 写入 `docs/TASK-STATUS.md`；禁止手改，只由渲染再生。
- 成功标准：消灭 Registry / RECEIPT-LOG 与 `.task/` 的 pending/done 双写漂移。手写文档状态不是权威。
- 负向：无 `.task/` 时 `--markdown` / `--write` 失败且不写文件。

## 0.27 — brief + handoff（2026-09-07）

- `taskctl.py brief TASK-xxx --role worker|scout|verifier`：从 manifest + round.json 生成标准简报，供 M1 贴进真窗或子代理。
- `taskctl.py handoff`：输出 M1 接班简报（任务状态、attempt、未闭环、BLOCKERS、hook-runs 摘要、下一步）。
- 成功标准：工人 / 新 M1 只凭生成物执行，不依赖手写转述。负向：缺失 task、非法 role 拒绝。两条命令都不改状态。

## 0.26 — 规则收敛（2026-09-07）

- SKILL.md 只保留当前规则；0.20～0.25 历史块移入本文件；G0～G3 / schema / transition 表移入 `references/task-gate.md`。
- 确认 `verification_policy()` 为 Gate、`audit-round`、`transition`、M1 收口的唯一策略实现；窗口轴与任务轴分离写进当前规则。
- 策略无法唯一决定收口路径，或 manifest 自相矛盾时输出 `POLICY_CONFLICT`，并有正负向测试。
- 立刻补丁（不另占版本）：测试用 `Path(__file__).parent / "taskctl.py"` 自解析；新增 `taskctl.py selftest`；`hooks.md` 每宿主补一行能力确认测试命令。

## 0.25 — 并行协作与加分判定

- 可用宿主已有子代理减少传话；不新增角色或窗号；M1 仍是唯一收口。
- 加分改为「本窗已确认的额外能力」；加分句从默认开场拿掉；未确认禁止自称加分。
- G2 忽略 `taskctl` 自管文件：`manifest.json` / `rerun.json` / `verify-report.json` 未列入工人 `changed_files` 不算漏报。
- 路径归一化只去掉 `./` 前缀，保留 `.task/` 这类点目录名。

## 0.24 — Hook 可观测性

- `hook-audit` 写 `phase=start` 与 `phase=end`，共用 `run_id`；字段含 `host`、`source`、`project_root`、`round_id`。
- `.task/hook-runs.jsonl` 最多保留 100 行，应 gitignore。
- Hook 失败只记账，不 `transition`、不标 done。

## 0.23 — 证据可重跑

- Full Gate / `verified` / `integrated` / `done` 时真跑验收命令，核 evidence 路径。
- 有 Git 时核工作区：落在 `allowed_paths` 内但未申报 → FAIL。
- 结果写入 `.task/TASK-xxx/rerun.json`。口头 PASS 无效。`--basic` 不重跑。

## 0.22 — 任务门禁

- 引入 `.task/TASK-xxx/manifest.json`、R 编号、`taskctl.py`。
- 状态机：`pending → in_progress → worker_done → (verifying → verified) → integrated → done`。
- P1：低风险且 `attempt < 2` 允许 M1 短路径 `worker_done → integrated`。
- P2：`--root` 必须在子命令前面。
- P3：`status` 在窗口行旁标注任务终态，不改写 `window_status`。
- P4：各宿主 Stop hook 经适配层调用 `hook-audit`。

## 0.20 / 0.21

- 多窗口编号、Registry / RECEIPT-LOG、斥候→主力→搜剿、卡点上限 4、查收以本窗重跑为准。
- 0.21 起有可复制的 `taskctl.py` 雏形；0.22 起成为正式任务控制层。
