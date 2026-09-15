# testdata — 双方共同运行的 `.task/` 契约样本

存在目的只有一个:**让 Codex 宿主与 DSH 宿主对同一份 `.task/` 给出相同结论**。
`taskctl.py` 是共享实现,但两个宿主会各自演化;只有固定样本 + 固定期望输出才能发现分叉。

## 目录内容

| 路径 | 是什么 | 角色 |
|---|---|---|
| `deskkit-sandbox/` | **v0.25 历史沙盒**(其 `TEST-RECORD.md` 自证 v0.25,源自 `D:\Cursor_projectt\test-v0.25-cursor`) | **旧版兼容 + 负例** |
| `build_fixtures.py` | 现场生成"当前规则正例"并导出冻结期望 | 生成器 |
| `expected-v035/expected-manifest.json` | 冻结的期望产物(纯数据,无嵌套仓库) | **正例期望** |
| `records/` | 旧轮测试记录存档 | 历史 |

### 负例:`deskkit-sandbox`(期望**失败**,不要修好它)

`TASK-003` 的 manifest **缺 `source_refs`**。需求覆盖(`REQUIREMENT_COVERAGE_FAIL`)是**后续版本才加的规则**,
但这条检查对**所有 schema 无差别执行**——所以它不是"样本坏了",而是"**旧版项目按现行规则理应不合格**"。

**不要给它补 `source_refs`。** 见 Codex 复核结论:*"它不是当前规则的正例,不应偷偷补字段;应把它登记为
旧版兼容负例,或迁移后另做一份 0.35 正例样本。"* 后者就是 `build_fixtures.py` 生成的东西。

它同时验证了另一件重要的事:**schema 回归守卫不会误伤它**——它没有 `.task/skill-lock.json`
(项目契约版本 = 0),所以"文件缺 `schema_version`"被正确理解为历史遗留,而不是被旧宿主改写。

### 正例:由 `build_fixtures.py` 生成

```text
py -3 testdata/build_fixtures.py
```

样本生成在**仓库外**的临时目录(`%TEMP%\mw035-contract-sample`),同时把冻结期望导出到
`testdata/expected-v035/`。脚本是**幂等**的:已存在则 `git reset --hard` + `git clean -qfdx`
重置到基线提交再重放全部步骤,任一步失败即中止并指出位置。

## 为什么正例不放在仓库里(三个坑,踩过才定下来)

1. **样本必须自带 git root。** 否则 `find_project_root()` 会向上找到**仓库自己的 `.git`**,
   把命令全部打到仓库根上。实测踩过:用 `--root testdata/compliant-v035` 跑 `init`,
   结果建出了 `D:\multi-window_M\.task\TASK-001`,污染了仓库。
2. **可一旦它自带 git root,放进仓库就会被记成 embedded repo / gitlink。**
   `git add` 会明确警告 `adding embedded git repository`,上游会多出一个无法正常 clone 的条目,
   而且 `.gitignore` **挡不住** `.git` 文件。两难 → 所以样本只能生成在仓库外。
3. **样本的验收命令必须不依赖第三方包。** 本机没有 `pytest`(实测不在 PATH),共同契约样本不能是
   宿主相关的 → 用 `py -3 -m tests.test_greet`,测试文件自带 `__main__`。
   用 `-m <模块>` 而不是 `<路径>.py`:前者把 cwd(项目根)加进 `sys.path`,
   `from src.greet import ...` 才能解析;后者只会把 `tests/` 加进 path。

另外两个必须记得的配置:样本的 `.gitignore` 要含 `__pycache__/`(否则跑一次验收命令就多出
"未申报改动",样本再也过不了 Gate);Windows 上 `.git/objects` 是只读文件,清理只能用 git 自己
`reset --hard` + `clean -qfdx`,不能 `rmtree`。

## 怎么跑

```text
# 负例:期望 REQUIREMENT_COVERAGE_FAIL
py -3 scripts/taskctl.py --root testdata/deskkit-sandbox status
py -3 scripts/taskctl.py --root testdata/deskkit-sandbox audit-round

# 正例:生成到仓库外,再对生成物跑
py -3 testdata/build_fixtures.py
py -3 scripts/taskctl.py --root "$TEMP/mw035-contract-sample" status
py -3 scripts/taskctl.py --root "$TEMP/mw035-contract-sample" gate TASK-001
py -3 scripts/taskctl.py --root "$TEMP/mw035-contract-sample" audit-round
```

## 实测期望输出(以 DSH 侧 0.35 为准;双方应一致)

### `deskkit-sandbox`(负例)

```text
$ taskctl.py --root testdata/deskkit-sandbox status
ROUND ROUND-002
WINDOW C1: worker_done  tasks=TASK-003:done note=tasks_closed
TASK TASK-001: done / GATE_PASS
TASK TASK-002: done / GATE_PASS
TASK TASK-003: done / GATE_PASS

$ taskctl.py --root testdata/deskkit-sandbox audit-round
REQUIREMENT_COVERAGE_FAIL
- REQUIREMENT_COVERAGE_FAIL: TASK-003: missing source_refs
```

注意:`status` 显示 `GATE_PASS` 是**从 `rerun.json` 读到的历史记录**,不是本次重跑——0.35 起
`status` 是只读视图,不执行任何 shell 命令。

### 正例(生成物)

```text
$ taskctl.py --root <生成目录> status
ROUND ROUND-001
WINDOW M2: worker_done  tasks=TASK-001:done note=tasks_closed
WINDOW C1: verified  tasks=-
TASK TASK-001: done / GATE_PASS

$ taskctl.py --root <生成目录> gate TASK-001
RESULT PASS

$ taskctl.py --root <生成目录> audit-round
BASIC_GATE_PASS
FULL_GATE_PASS
ROUND_READY_TO_CLOSE
```

样本刻意**停在收口前**(`.task/round.json` 仍在位),便于反复跑。`round-close` 的归档路径由
`scripts/test_dsh_035.py` 的 `R-*` 断言单独覆盖,不占用样本。

## 冻结期望里哪些是契约、哪些不是

`expected-v035/expected-manifest.json` 含两类东西,比较时**只比契约部分**:

**契约(两宿主必须一致)**
- `schema_version` 与 `skill_version`
- `expected_tokens`(`gate` → `RESULT PASS`;`audit_round` → 三个 token)
- `manifest` 的**结构性字段**:`task_id` / `owner` / `track` / `risk` / `status` / `allowed_paths` /
  `source_refs` / `requirements`,以及 `status` 的**迁移路径**(pending → in_progress → worker_done →
  verifying → verified → integrated → done)

**非契约(必然不同,不要比)**
- `generated_at`
- `status_history[*].at`(时间戳)
- `attempt` 之外的任何计数器在多次重跑后的值

## 与 `scripts/test_*.py` 的分工

| 层 | 文件 | 作用 |
|---|---|---|
| 宿主侧验收测试 | `scripts/test_retry_ladder.py`(Codex)、`scripts/test_dsh_035.py`(DSH) | 断言**本宿主**行为;不进对方的必跑集 |
| 跨宿主契约样本 | 本目录 | 双方都能跑、期望输出写死在这里 |

`test_dsh_035.py` 是 **DSH 侧**测试,断言的是 DSH 新增 API(`round-close`、`schema_version`、
`SCHEMA_REGRESSION_RISK`),**不要拿去 Codex 侧跑**——它必失败。真正跨宿主共用的是
`deskkit-sandbox`(负例)与本目录的冻结期望(正例)。
