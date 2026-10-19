# recover-factor-supply-losses 实施计划（2026-10-19）

> 对应 delta：`openspec/changes/recover-factor-supply-losses/`（proposal / design / tasks）。
> 执行方式：TDD 先红后绿；每任务独立测试文件；收口跑全量因子套件 + ruff + mypy。

**Goal:** 回收供给线可机械恢复的候选损耗（负数常量 6 + 深度 3 + 转译重试 2），并给「已考/未考成」族一条可审计的重放通道。

**Architecture:** 翻译器加严格等价重排表（`_rewrite_negation`）；DSL 深度上限 6→8（常量 + 注释 + prompt 同步并发布）；收割桥加合同失败重试（`TRANSCRIPTION_MAX_ATTEMPTS=3`，网关异常不重试）与 `retry_statuses` 按状态重放（新裁决覆盖 + `retried_from` 留痕，重放优先于未处理候选）；ops 脚本 `scripts/replay_factor_harvest.py` 承接运维入口。

**Tech Stack:** 纯 Python（零新依赖）；复用 `factors/{qlib_translate,dsl,harvest,external}.py` 既有纪律。

## Global Constraints

- 严格等价：重排不得改变任何输入的输出值（含 NaN/0 边界）；负窗口（前瞻引用）永在拒收面内
- 幂等不变式：`retry_statuses=None`（日批形态）行为逐字不变；重复运行 MUST NOT 重复导入
- fail loud 不变式：LLM 网关级异常上抛（不重试、不静默跳过、不记状态）
- 闸门零改动：五道闸、闸②门槛 0.3、自由参数上限 3 全部不动
- 复跑纪律：测量性重放一律临时 `--state/--db`，不写生产状态与生产因子库

---

### Task 1: 负数常量严格等价重排

**Files:** Modify `src/finance_agent/factors/qlib_translate.py`；Test `tests/factors/test_qlib_translate.py`

- [x] 先红：重排矩阵用例（8 种形态）+ 数值等价用例 + 负窗口红线用例 → 失败
- [x] 后绿：`_rewrite_negation` 重排表 + `_NegNumber` 叶子 → `sub(0,k)`；新增 `_QLIB_TO_BINOP` 归一（调用形态与中缀共用重排表，review 修复）
- [x] 回归：既有 132 用例全绿（含 `-1 * $close` 原「拒收」用例翻案）

### Task 2: 深度上限 6 → 8

**Files:** Modify `src/finance_agent/factors/dsl.py`、`src/finance_agent/prompts/factor_proposal.md`；Test `tests/factors/test_dsl.py`、`tests/factors/test_propose.py`、`tests/factors/test_qlib_translate.py`

- [x] 先红：`MAX_AST_DEPTH == 8`、深度 9 拒收、深度 7 供给线形态通过 → 失败
- [x] 后绿：常量 + 注释 + 两处文档串；越界用例统一加深两层（含提案路径连带用例）
- [x] prompt「≤ 6」→「≤ 8」；Langfuse 定向发布（`--exclude` 其余 16 个）→ production v4 取证

### Task 3: 转译合同失败重试

**Files:** Modify `src/finance_agent/factors/harvest.py`；Test `tests/factors/test_harvest.py`

- [x] 先红：重试成功（attempts=2）/ 重试耗尽（attempts=3 且整批继续）/ 载荷合同失败同走重试 / 网关异常调用计数=1 → 失败
- [x] 后绿：`_transcribe_with_retry`（while + 末次 re-raise，不引 assert——S101 纪律）+ verdict `attempts` 留痕

### Task 4: 按状态重放

**Files:** Modify `src/finance_agent/factors/harvest.py`；Create `scripts/replay_factor_harvest.py`；Test `tests/factors/test_harvest.py`、`tests/scripts/test_replay_factor_harvest.py`

- [x] 先红：覆盖 + `retried_from` / 重放优先 / 未命中状态不动 / 缺省幂等 → 失败
- [x] 后绿：`retry_statuses` 选择（replay + fresh 截断）+ `_with_retried_from` helper + summary `replayed`
- [x] ops 脚本：dry-run / 状态迁移对照 / 退出码 2 族用例

### Task 5: 测量与证据（已完成；review 修复见 tasks.md T6）

**Files:** Create `tests/validation/2026-10-19-recover-factor-supply-losses-replay.py`、验证报告

- [ ] R2：translator 阶段不可翻译族复译 → 恢复条数 + 过闸读数（临时库）
- [ ] R1：已拒族存库表达式复检 → 裁决 diff（临时库）
- [ ] R4：`transcription_error` 两条实网重试（私有临时 state）
- [ ] 读数落 `tests/validation/2026-10-19-recover-factor-supply-losses-validation.md`

### 收口

- [x] `uv run pytest tests/factors tests/scripts/test_replay_factor_harvest.py tests/scripts/spike/test_qlib_translate.py tests/test_api_factors.py tests/test_api_screening.py -q` → 423 passed
- [x] ruff 干净；mypy 四文件无问题
- [x] `openspec validate recover-factor-supply-losses --strict` 通过
- [ ] 提交 + PR；sync + archive 在验证通过后执行
