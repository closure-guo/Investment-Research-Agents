# add-factor-library-and-monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地因子库（SQLite 状态机 + 双层查重）、DSL 向量化求值引擎（消费 market-snapshot）、五道闸准入管线（可计算性→ICIR→置换/BH-FDR→池级增量→样本外）、分桶保种与衰减监控日批。

**Architecture:** 全纯函数计算层 + SQLite 存储层，分离清晰：`eval_engine.py`（AST→面板向量化求值、RankIC、远期收益，零 IO）；`validate.py`（五道闸纯函数，输入求值结果与统计量）；`library.py`（SQLite 存储/状态机/查重/保种/事件）；`monitor.py`（滚动 IC 降级规则 + 日批入口，注册进 ops 注册表）。数据面板经 `build_panel_from_rows` 从快照行构造（前复权口径换算），测试全部用合成面板（种子固定），零网络。

**Tech Stack:** Python 3.14 / pandas（向量化）/ sqlite3 / pytest。

## Global Constraints

- 测试：`uv run pytest tests/factors -q`；ruff/mypy 同仓库纪律。
- TDD 红→绿；既有测试零修改（ops 注册表扩容的计数断言按 change 1 先例最小更新并在报告披露）。
- 阈值/常量单一来源：`src/finance_agent/factors/constraints.py`（新）——`MAX_MISSING_RATE=0.2`、`MIN_ABS_ICIR=0.3`、`BH_FDR_Q=0.1`、`MAX_IC_CORRELATION=0.7`、`BUCKET_TOP_K=10`、`DECAY_WINDOW=60`、`DECAY_NEGATIVE_DAYS=20`、`DECAY_ICIR_FLOOR=0.1`、`PERMUTATION_N=200`、`TRAIN_FRACTION=0.7`、`DEFAULT_IC_HORIZON=1`。
- 原因码（逐字，跨模块契约）：`LOW_COVERAGE` / `WEAK_ICIR` / `NOT_SIGNIFICANT` / `REDUNDANT_WITH_POOL` / `NO_POOL_INCREMENT` / `OOS_FAILED` / `STRUCTURAL_DUPLICATE`。
- 状态机（逐字）：`candidate` / `active` / `degraded` / `retired`；合法迁移：candidate→active、active→degraded、active→retired、degraded→retired、degraded→active（复核恢复）、retired→∅；非法迁移抛 `FactorStateError`。
- 库文件：`FACTOR_LIBRARY_DB_PATH`（默认 `data/factor_library.db`），连接纪律同 `data/snapshot.py`（WAL/busy_timeout/自初始化/调用期读 env）。
- 求值口径：价格字段用**前复权**（`close_qfq`；open/high/low 用当日复权因子 `close_qfq/close_raw` 换算）；volume/amount 原样。面板=date×code DataFrame 字典。
- 零真实网络：验证任务用合成面板（固定种子）。

## 关键设计决策（实施者必读）

**E1 — 求值语义**：时间序列算子（`ts_*`/`delta`）沿日期轴按 code 计算（`min_periods=window`，不足 → NaN）；横截面算子（`rank`/`zscore`）沿 code 轴按日期计算（NaN 不参与排序，`rank` 用 `pct=True` 平均秩）；逐元素算子 NaN 传播；`div` 分母 0 → NaN（不抛）；`log` 非正 → NaN。`ts_corr(a,b,w)` = 滚动皮尔逊相关。全部向量化（无逐 code 循环——`groupby`/`rolling` 允许）。

**E2 — IC 定义**：`rank_ic_series(factor_values, forward_returns)`：逐日对全市场做 Spearman 相关（= 因子值秩与远期收益秩的 Pearson）；任一日有效样本 < `MIN_IC_SAMPLES=30` → 该日 NaN（样本太少不可信）。`forward_returns(close_qfq, horizon=1)` = `close_qfq.shift(-horizon)/close_qfq - 1`（末日 NaN）。IC 序列仅取**两端都有值**的日期。

**E3 — 闸②判定**：`|ICIR| ≥ 0.3` 且 `IC 均值 ≠ 0`（`|mean| > 0`，显著性由闸③承担）；ICIR = mean(IC)/std(IC)（std=0 → ICIR=0）。**闸①**：候选在全样本面板上的缺失率 ≤ 0.2（缺失率 = NaN 占比，含算子不足窗口的 NaN；按全体 cell 计）。

**E4 — 闸③置换检验**：同批次候选的 IC 序列分别做 `PERMUTATION_N=200` 次「逐日横截面重排因子值」置换（重排后重算 IC 均值），p = `(1 + #{|perm_mean| ≥ |obs_mean|}) / (1 + N)`（双侧）；同批次 p 值经 Benjamini-Hochberg（q=0.1）校正，`p_adj > q` → `NOT_SIGNIFICANT`。随机种子由调用方传入（`seed: int`），**同种子可复现**（测试钉死）。

**E5 — 闸④池级增量**：`pool_increment(candidate_ic, pool_ic_series, forward_returns, factor_values_map)`：池等权组合 = active 池各因子逐日横截面 zscore 后等权平均 → 组合值与远期收益的 RankIC 序列 → IR = mean/std；增量 = IR(池+候选) − IR(池)；`≤ 0` → `NO_POOL_INCREMENT`（空池时增量 = IR(候选) 与闸②重复但保留判定）。冗余：候选与池内任一因子 IC 序列 Pearson 相关 `|ρ| > 0.7` → `REDUNDANT_WITH_POOL`（记录相关对象 code/id）。闸④先查冗余再算增量（冗余拒收更省计算）。

**E6 — 闸⑤样本外**：按时间切分 `TRAIN_FRACTION=0.7`（前 70% 交易日训练，后 30% 验证，**切点按日期排序不跨日**）；验证段重算 IC 序列，`|ICIR_val| ≥ 0.3` 且均值符号与训练段一致 → 通过，否则 `OOS_FAILED`。切分口径与 decision-backtest 时点截断一致：任何统计量只用 `date ≤ 切点` 的信息（实现上分别取子区间计算，不做跨区标准化）。

**E7 — 查重**：结构层 `is_subtree_isomorphic`（规范化后）对库内全部因子（含 retired？**只查 candidate/active/degraded**，retired 允许重提）；数值层 IC 相关（属闸④）。`STRUCTURAL_DUPLICATE` 在**入库前**独立执行（先于五道闸），记录同构对象 id。

**E8 — 分桶保种**：`apply_bucket_quota(db, category)`：桶内 active 按 `pool_increment` 降序（并列 → created_at 早者优先）取 top `BUCKET_TOP_K=10`，超出者 `retired`；返回被退役列表。其他桶不动（测试钉）。

**E9 — 衰减监控**：`run_factor_monitor(panel, as_of, db_path=None, horizon=1) -> dict`：① 为每个 active 因子计算当日 RankIC 并 append 到 `factor_ic`（幂等：同 (factor, date) 覆盖）；② 对每个 active 因子取最近 `DECAY_WINDOW=60` 个交易日 IC 序列（不足 60 → 跳过，不判降级）；③ 滚动窗口（窗口=60，逐日滚动）均值序列取最后 `DECAY_NEGATIVE_DAYS=20` 个：**全部 < 0** 且每点 `|mean/std| < 0.1`（std=0 → ICIR 记 0）→ 置 `degraded` + 事件落库（含滚动 IC/ICIR 快照值）。降级后当日打分不含该因子（由消费者过滤 `status='active'`；本 change 提供 `list_active_factors`）。
- ops 注册：`factor_decay_monitor` job（15:45 mon-fri），`_with_retry` 包装；计数断言最小更新（5→7→6？按 change 1 先例：当前 6 → 7）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/factors/constraints.py`（新） | 全部阈值常量单一来源 |
| `src/finance_agent/factors/eval_engine.py`（新） | MarketPanel、`evaluate`、`build_panel_from_rows`、`rank_ic_series`、`forward_returns` |
| `src/finance_agent/factors/library.py`（新） | SQLite：schema/状态机/CRUD/结构查重/保种/事件/`list_active_factors` |
| `src/finance_agent/factors/validate.py`（新） | `GateResult`、五道闸、`admit_candidates(...)` 编排、BH-FDR、置换检验、池增量 |
| `src/finance_agent/factors/monitor.py`（新） | `run_factor_monitor` + 降级规则 |
| `src/finance_agent/outcome/ops/jobs.py`、`outcome/scheduler.py`（改） | 注册 `factor_decay_monitor`（15:45） |
| `tests/factors/test_eval_engine.py`、`test_library.py`、`test_validate.py`、`test_monitor.py`（新） | 单测 |
| `tests/validation/2026-10-13-add-factor-library-and-monitor-*.py/.md`（新） | 5 有效 + 5 噪声对照验收 + 报告 |

## Interfaces（逐字）

```python
# eval_engine.py
@dataclass(frozen=True) class MarketPanel:
    fields: dict[str, pd.DataFrame]     # key ∈ 六字段；value: index=date(str), columns=code, 前复权口径
def evaluate(node: AstNode, panel: MarketPanel) -> pd.DataFrame
def build_panel_from_rows(rows: pd.DataFrame) -> MarketPanel   # market_snapshot 行（列含 trade_date/code/open/high/low/close_raw/close_qfq/volume/amount）
def forward_returns(close_qfq: pd.DataFrame, horizon: int = DEFAULT_IC_HORIZON) -> pd.DataFrame
def rank_ic_series(factor_values: pd.DataFrame, fwd_returns: pd.DataFrame, *, min_samples: int = MIN_IC_SAMPLES) -> pd.Series

# library.py
class FactorStateError(ValueError): ...
def init_library(db_path=None) -> Path
def add_factor(*, expression: str, hypothesis: str, category: str, status: str = "candidate",
               ast_json: str | None = None, source: str = "proposal", alignment_score: float | None = None,
               db_path=None) -> int                      # 结构查重由调用方先做；此处只做状态合法性
def set_status(factor_id: int, status: str, *, detail: dict | None = None, db_path=None) -> None   # 校验迁移合法性 + 事件落库
def find_structural_duplicate(ast_json: str, *, db_path=None) -> int | None
def record_ic(factor_id: int, trade_date: str, rank_ic: float, db_path=None) -> None   # 幂等 upsert
def list_ic_series(factor_id: int, *, limit: int | None = None, db_path=None) -> list[tuple[str, float]]
def list_factors(*, status: str | None = None, category: str | None = None, db_path=None) -> list[dict]
def list_active_factors(*, category: str | None = None, db_path=None) -> list[dict]
def apply_bucket_quota(category: str, *, k: int = BUCKET_TOP_K, db_path=None) -> list[int]   # 返回退役 id
def set_pool_increment(factor_id: int, value: float, db_path=None) -> None
def list_events(factor_id: int | None = None, db_path=None) -> list[dict]

# validate.py
@dataclass(frozen=True) class GateResult:
    gate: int; passed: bool; reason: str | None; detail: dict
@dataclass(frozen=True) class AdmissionResult:
    factor_id: int | None; status: str; gates: tuple[GateResult, ...]; rejected_gate: int | None
def check_computability(factor_values: pd.DataFrame) -> GateResult
def check_single_factor(ic: pd.Series) -> GateResult
def check_significance(ic_series_map: dict[str, pd.Series], *, seed: int) -> dict[str, GateResult]
def check_pool_increment(candidate_ic: pd.Series, candidate_values: pd.DataFrame, pool: list[dict],
                         panel: MarketPanel, *, db_path=None) -> GateResult
def check_out_of_sample(factor_values: pd.DataFrame, fwd: pd.DataFrame) -> GateResult
def admit_candidates(candidates: list[dict], panel: MarketPanel, *, seed: int, db_path=None) -> list[AdmissionResult]
    # candidates: [{"expression","hypothesis","category","ast_json"}...]；编排：结构查重→闸①②③→闸④→闸⑤→入库(active)→保种

# monitor.py
def run_factor_monitor(panel: MarketPanel, as_of: str, *, db_path=None) -> dict   # {"checked","degraded":[...],"skipped":n}
```

---

### Task 1: 求值引擎

- [ ] **Step 1: 写失败测试**（`tests/factors/test_eval_engine.py`，合成小面板手算对拍）：每个算子至少一例（`ts_mean/ts_std/ts_corr/ts_rank/delta/rank/zscore/add/sub/mul/div/max/min/abs/log`）手算期望值；`min_periods` 边界（窗口不足 → NaN）；横截面 vs 时序轴正确性；`div` 除零/`log` 非正 → NaN；`build_panel_from_rows` 的 qfq 换算（构造 close_raw≠close_qfq 行，断言 open_qfq = open×qfq/raw）；`forward_returns` 末日 NaN；`rank_ic_series` 手算 Spearman + `min_samples` 门槛。
- [ ] **Step 2–4: 红 → 实现 → 绿**（E1/E2；向量化）。
- [ ] **Step 5: Commit** `feat(factor-lib): vectorized DSL evaluation engine + RankIC (T1)`。

### Task 2: 库 schema + 状态机 + 结构查重

- [ ] **Step 1: 写失败测试**：三表 DDL（factors/factor_ic/factor_events）幂等；`add_factor` 状态校验（非法初始状态抛错）；`set_status` 合法迁移全通过、非法迁移抛 `FactorStateError`；`find_structural_duplicate`（库内 `ts_mean(close,10)` → 提交 `ts_mean(close,11)` 命中 id；`ts_mean(volume,10)` 不命中；retired 不参与查重）；`record_ic` 幂等；`apply_bucket_quota` 桶内 11 个 → 退役 pool_increment 最低者、并列取 created_at 早者、其他桶不动；`list_events` 审计。
- [ ] **Step 2–4: 红 → 实现 → 绿**（E7/E8）。
- [ ] **Step 5: Commit** `feat(factor-lib): SQLite library with state machine, dedup and bucket quota (T2)`。

### Task 3: 闸①② + 编排骨架

- [ ] **Step 1: 写失败测试**：`check_computability`（缺失率 19.9% 通过 / 20.1% `LOW_COVERAGE`）；`check_single_factor`（|ICIR| 0.31 通过 / 0.29 `WEAK_ICIR` / 均值 0 `WEAK_ICIR`）；`admit_candidates` 骨架（结构重复 → `STRUCTURAL_DUPLICATE` 且不入库；①② 通过后进入闸③占位（本任务先实现到②为止，③④⑤ 以最小桩返回 passed 并在 T4/T5 替换——**桩必须显式标注 `TODO(T4)` 并在 T4 删除**）。
- [ ] **Step 2–4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(factor-lib): gates 1-2 + admission orchestration skeleton (T3)`。

### Task 4: 闸③置换检验 + BH-FDR

- [ ] **Step 1: 写失败测试**：同种子两次运行 p 值逐位相等；已知强信号 IC → p 小通过；纯噪声 IC → `NOT_SIGNIFICANT`；BH-FDR 单调性（批量 50 个 p 值 → q 值单调不减，手算小样本对照）；**50 个随机表达式批量注入**（种子固定，合成噪声面板）→ 入库 active < 5。
- [ ] **Step 2–4: 红 → 实现 → 绿**（E4；删除 T3 桩）。
- [ ] **Step 5: Commit** `feat(factor-lib): permutation test + BH-FDR significance gate (T4)`。

### Task 5: 闸④池级增量 + 闸⑤样本外

- [ ] **Step 1: 写失败测试**：冗余（|ρ|=0.92 → `REDUNDANT_WITH_POOL` 且 detail 记相关对象 id）；增量 ≤0 → `NO_POOL_INCREMENT`；增量 >0 通过；空池行为；闸⑤训练段强验证段弱 → `OOS_FAILED`；两段皆强 → 通过；切分点不跨日（断言 train/val 日期集合无交集且并集 = 全区间）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（E5/E6；删除 T3 桩）。
- [ ] **Step 5: Commit** `feat(factor-lib): pool-increment and out-of-sample gates (T5)`。

### Task 6: 衰减监控 + 日批接线

- [ ] **Step 1: 写失败测试**（`tests/factors/test_monitor.py`）：60 日窗口不足 → skipped；构造「连续 20 日滚动均值为负且 |ICIR|<0.1」序列 → degraded + 事件含滚动快照；仅 19 日 → 不降级；20 日中 1 日为正 → 不降级；降级后 `list_active_factors` 不含该因子；`record_ic` 幂等（同日重跑不重复）。ops：`JOB_IDS` 含 `factor_decay_monitor`、SCHEDULES 15:45 mon-fri、`_with_retry` 包装（计数断言 6→7 最小更新）。
- [ ] **Step 2–4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(factor-lib): decay monitor with rolling-IC degradation + daily job wiring (T6)`。

### Task 7: 对照验收 + 收口

- [ ] 合成面板（`tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py`，固定种子）：迭代生成价格过程，使「动量 / 反转 / 波动 / 流动性（成交额水平代理）/ 量价协同」五个经典因子对下一期收益有真实预测成分（生成过程显式依赖这些量）；另 5 个噪声因子（随机表达式）。跑 `admit_candidates`：**断言 5 经典因子全部 active、5 噪声全部被拒**，逐条记录被拒闸口与原因码，落报告。
  - 口径注记（诚实披露）：经典因子清单中的「市值/换手」因 DSL 字段集（价量六字段，无股本数据）不可直接表达，用「成交额水平（流动性）」与「量价协同」替代，报告写明替换理由。
- [ ] `uv run pytest tests/factors -q` + 全量 `tests/ -m "not live"`（记录；既有 env flake 归因）；ruff/mypy。
- [ ] 报告 `tests/validation/2026-10-13-add-factor-library-and-monitor-validation.md`（命令证据、对照表、异常记录、结论）；`tasks.md` 全勾 + 逐条注明 →（控制器执行 archive）。
- [ ] Commit `docs(openspec): add-factor-library-and-monitor validation report + tasks 回填`。
