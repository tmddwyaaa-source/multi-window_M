# multi-window_M 历史版本

当前规则以 `SKILL.md` 的 `version` 字段为准。本文件只作历史说明，**不得当作当前规则引用**。

## 0.36-dsh rev3 — rev2 复核 4 条 + 迁移回滚（2026-09-18）

Codex 复核 rev2 又发现 4 条缺陷与 1 处回滚缺口（我先逐条独立复现，**6/6 成立**）。全部修正。

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | **丢失的基线可被 `brief` 用当前标准重建**：新版派工后删除 `contract.json`，`gate` 会报 LOST，但 `brief` 仍按当前标准重建基线并把新哈希写回 manifest → 原标准被悄然替换 | `ensure_contract()` 先看 manifest 是否**记录过**建立基线（`contract_baseline` 标记）；有记录而文件丢失 → 报 `ACCEPTANCE_BASELINE_LOST`，**拒绝自动创建**。只有**首次**合法派工才创建 |
| 2 | **接受依据太弱**：`mtime` 兜底不能证明审查针对当前变化；且归属/身份错误的报告（`task_id` 不对、`reviewer` 不是合法窗号、`result=fail`）只要 key 匹配也被接受 | **取消 mtime 兜底**，必须用 `contract_key` 绑定当前变化指纹；并校验报告**归属**（`task_id` 对应本任务）、**合法窗号**（`valid_window`）、**与实现者分离**、**验收路由一致**（需要独立验收时 reviewer 须为本轮指派 verifier）；还要求**针对本次变化的独立结论** `standard_verdict ∈ {equivalent, stricter, accepted}`——功能 `result` 不能代替变更审查结论。该值与差异由 **verifier 的 `brief`/`packet` 自动给出**，验收 agent 照抄，用户不需要知道字段名 |
| 3 | **轮次判据忽略已记录的新轮次**：用"最终状态值相同/不同"判断，`manifest.repair_cycle=2` 而上次失败 `repair_cycle=1` 时会算回 1 | 回到**显式、单调**的轮次记录：轮次只在**真实施工/交付事件**上推进（`transition` 进入 `in_progress`、重新交付 `worker_done`、硬停裁决解除）。判据只做单调回退保护 |
| 4 | **迁移回滚清单不完整 + 空文件语义错误**：不含 `skill-lock.json` 等契约写入；`b''` 被当成"原来不存在"，会把**空的既有文件删掉**；恢复失败被忽略后仍宣称已恢复 | 快照改为 `(原本是否存在, 原始字节)`；回滚清单覆盖**目标脚本 + 共同文件 + `skill-lock.json` + 迁移报告**；恢复失败**逐条列出**，不再宣称全部恢复 |

### 共享契约测试的修订（公开说明，待 Codex 复核）

`scripts/test_retry_ladder.py` 是**跨宿主共享**的契约测试。本次按 Codex 在 rev3 复核中的明确意见修改了它，
理由写在文件头部：

- 它原先在两处失败之间**直接改写 `manifest["status"] = "worker_done"`**，绕过 `transition`。
  那只是夹具在模拟"工人重新交付"，**不是生产设计要求**；在"轮次由真实施工/交付事件推进"的规则下，
  它不构成可识别的交付事件，于是第二次失败会被判为同一次失败的补充说明。
- 改法：把两处直接改写换成**真实的 `transition`**（`reopened -> in_progress` 重新施工；
  `worker_done` 重新交付）。
- **断言全部保留**，没有任何一条被削弱或删除：第二次失败要求换人（`REASSIGN_REQUIRED`、`blocked`、
  `attempt == 2`、BLOCKERS 记录）、第三次硬停（`HARD_STOP`、`block_history` 长度 3）、
  根因改名需证据、owner 交接与路由移动。结果 **5/5 通过**。

### 回归

`selftest` **18/18**（含上述共享契约测试）；`review_probes_dsh.py` / `rev2_gap_probes.py` /
`rev3_gap_probes.py` 三套探针全部退出码 0；`multi-window-m-035` 未动；0.36 仍未安装。

## 0.36-dsh rev2 — 独立复审 4 条的补修（2026-09-18）

Codex 对 rev1 的独立复审又发现 4 条缺陷（我先逐条独立复现，**4/4 成立**），另有 2 条交付说明问题。全部修正。

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | **派工点前提不完整**：需求与路径齐全但 `source_refs=[]` 时，`gate --dispatch` 仍通过并建基线 | 新增 `validate_dispatch_ready()` 作为**唯一实现**，顺序：身份 → 需求 → **来源映射** → 边界 → 策略 → 路由 → 才保存基线。`brief`/`packet` **共用同一套**校验（默认派工不能绕过）；首次派工严格校验路由，已在飞行中的任务重新出简报时路由降级为提示 |
| 2 | **接受缺少验收者判断证据**：无任何 verify-report 也能 `adjudicate accept` | 必须带独立验收者对**本次变化**的判断：`reviewer` 非 owner/worker，且报告 `contract_key` 等于当前变化指纹（或报告比 manifest 更新）。旧报告 `contract_key` 不一致时判 `ADJUDICATE_STALE_EVIDENCE`。接受记录留存**判断者、依据报告与哈希、变化指纹、原需求、来由（`--origin`）** |
| 3 | **失败身份仍含 reason**："同一轮换一种说法"会被重复计数 | 失败事件身份改为 **修复轮次 + 卡点 + 负责人**，**不含 `reason`**。轮次按**状态是否变过**判定：当前状态与"上次失败留下的状态"相同 → 同一次失败，只记补充说明（`FAILURE_SUPPLEMENTED`，不增加计数）；状态变了（有人重新施工或重新交付）→ 新失败，照常计数。换根因用不同 `block_id`。**这条同时满足 rev2 要求与既有的三次梯级契约**（Codex 的 `test_retry_ladder.py` 仍 5/5） |
| 4 | **基线缺失一律当历史例外**：新版派工后删掉 `contract.json` 也只提示不阻断 | manifest 增加最小标记 `contract_baseline`（派工时写入）。四种情形分开：历史任务**只在收口提示一次**；**新版派工过但基线消失 → 阻断**（`ACCEPTANCE_BASELINE_LOST`）；损坏 → 阻断；正常 → 对比。不再"用文件现在不存在推定过去从未存在" |

**交付说明问题（Codex §6）**

- `review_probes_dsh.py` 的 `check()` 只打印、`main()` 恒返回 0 → 改为**累计失败并返回非 0**。修好后立刻暴露出 7 项真实 FAIL（此前"全部通过"只靠肉眼读输出）。
- 迁移提交阶段补**回滚**：提交前记录所有将被改写文件的原始字节，中途 I/O 失败按原样恢复。
  于是"不半迁移"是被证明的，而不只是"输入预检失败不部分改写"。

**连带更新（测试跟随语义，非放松断言）**

- 梯级测试补上"恢复施工"这一步：第二次/第三次失败必须是**新一轮修复之后**的失败。
  同轮重复提交现在断言 `FAILURE_SUPPLEMENTED` 且计数不变。
- 原 `K-neg-fourth-failure-rejected` 改为两条更严格的事实：同轮第四次提交不计数 +
  **硬停后恢复施工被拒**（真正的第四次失败已无法发生）。
- `test_direction_acceptance.py` 增加"硬停后普通 reassign 被拒"正反例。

**回归**：`selftest` **17/17**；`review_probes_dsh.py` 全部通过（退出码 0）；
`rev2_gap_probes.py` 4/4 不再复现；Codex 的 `test_retry_ladder.py` **5/5**。

## 0.36-dsh — 修工具误报与恢复路径，不加新门禁（2026-09-16）

依据《0.36 方向文件》（Codex 撰写）与两份实测报告（0.35 门禁首跑、DEEPDIVE ROUND-004）。
**035 目录冻结不动**；本版是隔离新建副本，先在技能自动发现目录外（`~/.dsh/skill-builds/`）开发与验证。

### 修掉的真实缺陷

| # | 缺陷 | 证据 | 修法 |
|---|---|---|---|
| 1 | **失败计数与换人互相堵死**：`attempt --owner` 既计数又交接，把计数推到 3/3 自动 `blocked`；再想救回时 `reassign` 因"owner 已经是它"被拒 → **没有任何子命令能把卡点从 `blocked` 里救回来** | DEEPDIVE §2.1，**已实测致硬停**，且其中一次失败是 M1 自己消耗的 | 一次真实失败只计一次（失败指纹去重，重复提交报 `DUPLICATE_EVENT`）；换人不增加失败次数；重复的同一交接只确认（`HANDOFF_CONFIRMED`）；同负责人可确认已有交接，但**不算满足第二次真换人**。第三次硬停**保留**，并统一给出 `HARD_STOP` token 与非 0 退出码 |
| 2 | **`tests[].cmd` 被静默忽略**：工人写 `cmd` 而非 `command` → **15 条回归命令从未被重放**，门禁毫无提示 | DEEPDIVE 族 A2 | 新增 `UNKNOWN_FIELD`：指出**文件 + 条目 + 字段名 + 正确写法**（含相似度提示），不做静默容错；合法可选元数据不拒 |
| 3 | **迁移死锁**：`migrate-project` 先把锁升到 schema 1，再检查既有文件 → 报 20 条 `SCHEMA_REGRESSION_RISK`，**命中每一个从 ≤0.34 迁移上来的项目**（即 `migrate-project` 的全部目标用户） | 0.35 实测报告 §4.26，记为"最高优先级工具缺陷" | 顺序反转为 **备份 → 识别旧格式 → 转换（只补版本标记）→ 验证 → 最后才提交版本标记**；缺信息**明确报告不编造**；失败**绝不留下"成功标记 + 无法操作"的半迁移状态** |
| 4 | **`STALE_REPORT` 稳定假红**：4/4 任务命中，诱因是"流程自己刚写、尚未提交"的 `.task/` 产物进了 diff；标题会被读成"工人在偷改实现" | 0.35 实测报告 §2.5 | **取消 gate 与 round-close 的阻断**，只写日志 + `rerun.json` 留痕；不计失败次数 |
| 5 | **文档教了一个不存在的参数**：`gate --full`（SKILL.md、task-gate.md、taskctl.py docstring/help 共 7 处），照抄即报错 | 0.35 实测报告 §2.1（P0） | **不加 `--full` 别名**（别名是复杂度入口）；统一为 `gate <TASK-ID>`（默认即完整验收）与 `--basic`（基础验收）。<!-- 0.36-doc-note: 本行记载历史缺陷，不是教用法 --> 并新增自检：文档里出现该坏字符串（除本行豁免外）一律判失败 |
| 6 | **工人可静默改自己的 `verify_cmd` / `allowed_paths`**（自我验收漏洞） | 0.35 实测报告 §2.3，**真实发生** | 新增独立 `contract.json`（**工具自动生成**：`init` 与 `brief --role worker` 时落基线，M1 不需要额外命令）。收口时对比基线，变化报 `ACCEPTANCE_CHANGED`，把**原需求与原标准**交给验收者判断，M1 接受后才收口；**不断定是否放宽**，也**不宣称防作弊** |
| 7 | **`allowed_paths` 承诺与实现不符**：文档说会拦越界，实测只检查**已申报**的改动 | 0.35 实测报告 §2.4 | 新增**整轮越界识别** `UNDECLARED_CHANGE`：排除流程产物、门禁自有产物、**其他任务的合法改动**与用户既有改动之后，仍无法解释的改动才在收口时阻断；**归属不确定只提示**，不简单归罪某个工人 |
| 8 | **派工/收口不区分**：验收脚本是工人交付物，派工那刻必然不存在，却立刻报 `UNREPLAYABLE_COMMAND` 噪声 | DEEPDIVE 族 A1 | 改为**纯事实检查** `MISSING_ACCEPTANCE_SCRIPT`：派工不阻断；交付/收口只查存在性，缺失即失败。**绝不创建/覆盖/删除任何文件来制造验收前提**——曾一度用"临时落空壳再执行"实现，经 Codex 复核**否决并已移除**（占位改变被检查对象；空脚本可能退出 0，证明不了原命令可运行）。命令一律按原样执行 |
| 9 | 验收命令强度没人管 | DEEPDIVE 族 A（`node --check` 只做语法解析却一路放行） | `packet` 生成时给**明显偏弱**的形态打 `WEAK_ACCEPTANCE` 提示（同时看 manifest 与 `worker-report.tests[]`）。**仅辅助提示**：不判 FAIL、不影响收口、不代替验收者判断 |

### 新增诊断码

`DUPLICATE_EVENT`、`UNKNOWN_FIELD`、`ACCEPTANCE_CHANGED`、`UNDECLARED_CHANGE`、`MISSING_ACCEPTANCE_SCRIPT`、`WEAK_ACCEPTANCE`（其中 `UNDECLARED_CHANGE` 与 `MISSING_ACCEPTANCE_SCRIPT` 是收口阻断，`WEAK_ACCEPTANCE` 是纯提示）。

### 明确不做（写下来防止回流）

- 不加 `gate --full` 别名<!-- 0.36-doc-note: 记载"明确不做"，不是教用法 -->
- 不做 `MANIFEST_TAMPERED` 哈希锁 + 人工 `relock`（基线自动生成即可）
- 不把 `allowed_paths` 降级为"只记录不强制"（它是**协调机制**：防并行窗口误改别人的模块）
- 不用 mtime 判迁移（文件复制/还原会改变时间）
- 不新增 `shared_paths` 白名单（共享文件用**批次串行**解决）
- 不把测量值（token / 体积 / 命中率）放进 Gate
- **不创建占位/空壳/临时文件来制造验收前提**（改变了被检查对象；空脚本可能退出 0，证明不了原命令可运行；并发窗口还可能互相覆盖或误删）
- 不为"制造验收前提"而覆盖或删除业务文件、测试脚本

### 测试

- `selftest` **17 套全绿**。新增三套：
  - `test_migration_deadlock.py`（10 项）：复现并验证迁移死锁已修，含"失败不半迁移"
  - `test_direction_acceptance.py`（18 项）：按《0.36 方向文件》第 7 节逐项验收
  - `test_script_fact_check.py`（11 项）：按《0.36 验收修正》第三节五个场景验证，**每条都检查真实前后文件状态**（不只是断言 token/退出码）
- Codex 版 `test_retry_ladder.py` 在本版上**原样通过 5/5**：三次失败梯级的语义未被削弱。
- 被改动的既有断言（均已在测试里注明理由）：`K-pos-third-failure-hits-cap`（硬停现在给 token 与非 0）、`K-neg-reassign-same-owner-rejected`（拆成"有历史→确认"与"无历史→拒绝"两条）、`U-neg-missing-declared-file-is-blocked`（改为按真实执行结果阻断）、`D2-*`（stale 取消阻断）、`M-pos-lock-fields`（迁移结束状态为 `converted`）。

## 0.35-dsh — schema 契约、只读 status、关轮、不可重放命令（2026-09）

依据 `skill测试报告.md`（D:\8xgg\project 三轮真实交付，19 个任务）与 Codex 的《给 DSH：0.34 实测后的兼容性决策与实施顺序》。**0.34 目录保持不动**，本版是新建隔离副本。

### 解决的实际问题

| 问题 | 出处 | 处理 |
|---|---|---|
| `status` 会逐任务重跑 Gate（含 ~90s 回归命令），多任务时被 harness 120s 掐断 | 报告 §4.8 | **`status` 改为只读**：不执行任何 shell 命令，只显示上次真跑的记录（`GATE_PASS`/`GATE_FAIL`/`GATE_NOT_RUN`/`GATE_STALE`）。需要重算用显式 `--deep`。收口仍由 `gate <TASK-ID>`（默认即完整验收；**0.36 起文档不再宣称存在 `--full` 参数**）/ `audit-round` 负责 |
| 没有关轮命令，第二轮开不了，只能手工删 `round.json` | 报告 §2.3 | **新增 `round-close`**：仅当整轮任务全部 `done` 且审计通过，才把 `round.json` **原子归档**到 `.task/rounds/`；未完成任务一律拒绝；绝不自动触发 |
| `tests[].command` 写占位符 / 说明文字 / 已删文件，Full Gate 逐字重放 → 永久 FAIL | 报告 §2.9、§4.3、§4.7、§4.15（**同一根因出现 3 次**） | **新增 `UNREPLAYABLE_COMMAND` 预检**：占位符、全角/中文说明、无运行器前缀、引用已声明却缺失的文件 → 收口前直接报明码并指出条目。**它是验收证据错误，不推进卡点计数** |
| verify-report 会在文件被改后继续被当证据用 | 报告 §2.22 | **新增 `STALE_REPORT`**：报告自称的 `changed_files` 之后又被改过 → 报明码。判据用**内容指纹**，不用 mtime。**分层**：日常 `gate` / 例行 `audit-round` / hook 审计只**提示**；**只有 `round-close` 归档时阻断**。它只提示报告可能旧了，**不声称**已保证验收新鲜 |
| 两个宿主可能用各自旧的理解改写对方新写的数据 | Codex 决策 | **新增 `.task/` `schema_version`**：读到更高版本 → `UNSUPPORTED_SCHEMA`，**fail closed**：只允许查看，禁止改状态与收口；缺失/更低 → 走迁移兼容路径 |
| **旧宿主（0.34）会若无其事地操作新版项目**（Codex 复核指出：`schema_version` 只保护"向前"，不保护"向后"） | Codex 评审 | **新增 `SCHEMA_REGRESSION_RISK`**：`.task/skill-lock.json` 已声明契约版本 N，而**活动轮或任务目录**的 `schema_version` < N → 拒写。真正的历史项目（锁里也无版本声明）继续兼容；契约只在写了新文件后升级，**读取路径绝不自动补写** |
| 文档暗示 `reassign --window` | 报告 §2.2 | **保留准确的 `--owner`**（Codex 决定：`window` 会混淆 worker 与 verifier）。`assign-verifier --window` 专指 verifier，两者不混用。修正文档与模板 |

### 新增/变更的子命令与字段

- 新子命令：`round-close`。`status` 新增 `--deep`。
- 新诊断码：`UNSUPPORTED_SCHEMA`、`SCHEMA_REGRESSION_RISK`、`UNREPLAYABLE_COMMAND`、`STALE_REPORT`。
- 新字段：`.task/*/manifest.json` 与 `.task/round.json` 顶层 `schema_version`；`.task/skill-lock.json` 顶层 `schema_version`（项目契约声明）；`rerun.json` 新增 `files`（内容指纹）与 `stale_report`。
- `KNOWN_COMMANDS` 补齐原先缺失的 `assign-verifier` / `sync-worker-route` / `packet`（影响 `--root` 位置校验）。

### 修掉的真 bug（都是被测试逼出来的）

1. **`round-close` 会把"未就绪"当成"通过"归档。** `cmd_audit_round` 在 `ROUND_NOT_READY`（缺 receipt）时也返回 0，而 `round-close` 把"返回 0"当审计通过 → **归档了尚未就绪的轮**。现在收口路径下 `ROUND_NOT_READY` 与 `AUDIT SKIP` 都返回非 0，归档被拒绝。这正对应设计方案里点出的"提前归档 → 状态双写"风险。
2. **`STALE_REPORT` 分层放错位置。** 第一版把过期报告在**所有**场景都做成 WARN，结果在最需要它的地方（收口）失效了——它能提示"报告可能旧了"，却无法阻止拿过期证据过关。
3. **`SCHEMA_REGRESSION_RISK` 测试暴露的夹具不一致**：`test_block_staircase` 在 `init` 之后手写没有版本标记的 `round.json`，被守卫正确拦下。修的是夹具，不是放松守卫。
4. **`KNOWN_COMMANDS` 缺 3 个命令**（`assign-verifier` / `sync-worker-route` / `packet`），导致 `--root` 位置校验在这些命令上失效。

### 纳入的跨宿主共同原则（Codex 确认）

1. `.task/` schema、状态机、`owner`/`verifier`、`block_id`、三次卡点梯级与核心 Gate 是**跨宿主共同核心**。
2. DSH 的 `packet` / `read_budget` / 锚点诊断 / `dsh-runs.jsonl` / 宿主参数是**附加层**，不得改变共同字段含义或收口结论。
3. 新字段必须 optional 且有旧行为默认值。
4. Hook 只是账本和触发器：不改状态、不派工、不标 done。
5. **一任务只能有一个 `owner`**；多人协作拆子任务。
6. **M1 代验不算独立验收**：verifier 窗口缺席时任务保持未完成；只有派工前明确调整风险策略才可不要求 verifier。
7. 旧 manifest 缺 `block_attempts` 时回落任务级 `attempt`，标注 `LEGACY_UNSCOPED`，保守兼容**保留**。

### 测试

- 本分支 `selftest` 全绿：**15 套**（新增 `test_dsh_035.py`，**43 项断言**，每个改动都同时覆盖正例与负例）。
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
