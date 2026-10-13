# add-screening-ranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L2 横截面因子打分排序（确定性纯函数，Top-N + 逐因子贡献分解）+ builtin 手工因子基线包（免提案、同过五道闸）。

**Architecture:** `screening/ranker.py` 纯函数：消费 `MarketPanel`（change 6 求值引擎）+ 因子库 active 因子与其近 60 日 IC 统计 → 逐因子截面 z-score → 按 IC 方向×强度加权 → 综合分 + 贡献分解 → Top-N `Candidate`。`factors/builtin.py` 提供五个手工因子的 DSL 表达并复用 `admit_candidates`（source="builtin"）注册。零 IO、零随机。

**Tech Stack:** Python 3.14 / pandas / pytest；复用 `factors/{eval_engine,library,validate}`。

## Global Constraints

- 测试：`uv run pytest tests/screening tests/factors -q`；ruff/mypy 同仓库纪律。
- TDD 红→绿；既有测试零修改（全新增文件）。
- Candidate 契约（逐字）：`symbol / trade_date / composite_score / factor_contribs[{factor_id, zscore, weight, contribution}] / rank`；勾稽：`Σ contribution == composite_score`（容差 1e-9，spec 明文）。
- 默认 `top_n=100`（可配置）；排序 tie-break：composite 降序 → symbol 升序（确定性）。
- 打分零 LLM、零随机：同输入两次执行逐项相等（spec 场景）。
- builtin 五个（名称逐字）：`momentum_20` / `reversal_5` / `turnover_proxy` / `volatility_20` / `size_proxy`；`category="builtin"`、`source="builtin"`。
- 字段集限制（诚实口径）：DSL 无股本数据 → 「换手率」以量比代理（`div(volume, ts_mean(volume, 20))`）、「市值」以成交额水平代理（`ts_mean(amount, 20)`）；模块 docstring 与验证报告均须写明。

## 关键设计决策（实施者必读）

**R1 — z-score 口径**：逐因子、在**通过 L1 的标的集合**上做截面 z-score（`ddof=1`，与 `eval_engine.zscore` 同口径）；因子值 NaN（窗口不足/缺失）→ 该标的该因子 `zscore=0.0`（贡献 0，且不因缺失被剔除——L1 已负责可交易性）；截面全 NaN 或 std=0 → 全体 zscore=0.0。

**R2 — 权重口径**：`weight_i = ic_mean_i / Σ_j |ic_mean_j|`，其中 `ic_mean_i` = 该因子近 60 日 RankIC 均值（由调用方传入 `ic_stats: dict[int, tuple[float, float]]` → `(ic_mean, icir)`；`icir` 仅记录于 detail 不参与权重）。`Σ_j |ic_mean_j| < 1e-12` → `ValueError("无有效 IC 信息，拒绝打分")`（fail loud，不做任意排序）。
- `contribution_i = weight_i × zscore_i`；`composite_score = Σ_i contribution_i`（按此顺序累加，勾稽天然成立但必须被测试钉死）。

**R3 — as_of 语义**：`rank_universe` 只用 `panel` 中 `as_of` 当日行（不跨日聚合，无前视）；`as_of` 不在面板 → `ValueError`（含可用日期范围）。

**R4 — IC 统计来源**：调用方从因子库 `factor_ic` 表取近 60 个交易日（`list_ic_series(factor_id, limit=60)` 由 ranker 模块内 helper `recent_ic_stats(db_path, factors) -> dict[int, tuple[float,float]]` 组装；空序列 → `(0.0, 0.0)`，不参与权重分子但保留在分母？**不**：`ic_mean=0` 的因子权重 0，等价于不参与——文档写明）。

**R5 — builtin 注册**：`register_builtins(*, panel, seed, db_path=None) -> list[AdmissionResult]`：逐条 `admit_candidates`（`category="builtin"`、`source="builtin"`、`hypothesis` 为经济学描述文本）；已存在结构同构者 → 跳过并返回 `STRUCTURAL_DUPLICATE` 结果（幂等重跑不重复入库）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/screening/ranker.py`（新） | Candidate/FactorContribution、权重、打分、Top-N、recent_ic_stats |
| `src/finance_agent/factors/builtin.py`（新） | 五个 builtin 因子 + `register_builtins` |
| `tests/screening/test_ranker.py`（新） | 打分/勾稽/确定性/边界 |
| `tests/factors/test_builtin.py`（新） | 注册路径 + 同闸约束 |
| `tests/validation/2026-10-13-add-screening-ranker-*.py/.md`（新） | 3 交易日 Top-100 抽 10 手算核对 + 报告 |

## Interfaces（逐字）

```python
# screening/ranker.py
@dataclass(frozen=True) class FactorContribution:
    factor_id: int; zscore: float; weight: float; contribution: float
@dataclass(frozen=True) class Candidate:
    symbol: str; trade_date: str; composite_score: float
    factor_contribs: tuple[FactorContribution, ...]; rank: int
def factor_weights(factors: list[dict], ic_stats: dict[int, tuple[float, float]]) -> dict[int, float]
def recent_ic_stats(factors: list[dict], *, window: int = 60, db_path=None) -> dict[int, tuple[float, float]]
def rank_universe(*, panel: MarketPanel, as_of: str, passed_codes: Sequence[str],
                  factors: list[dict], ic_stats: dict[int, tuple[float, float]],
                  top_n: int = 100) -> list[Candidate]

# factors/builtin.py
@dataclass(frozen=True) class BuiltinFactor: name: str; expression: str; hypothesis: str
BUILTIN_FACTORS: tuple[BuiltinFactor, ...]      # 五个，名称/表达式逐字见 Global Constraints + R1 代理口径
def register_builtins(*, panel: MarketPanel, seed: int, db_path=None) -> list[AdmissionResult]
```

---

### Task 1: 打分纯函数

- [ ] **Step 1: 写失败测试**（`tests/screening/test_ranker.py`，小面板手算）：两因子三标的 → 手算 zscore/weight/contribution/composite 逐项断言；`Σ contribution == composite`（1e-9）；**确定性**（两次调用逐项相等）；NaN 因子值 → zscore 0 且该标的仍在列表；std=0 截面 → 全 0；`Σ|ic_mean| = 0` → `ValueError`；`as_of` 缺失 → `ValueError`；`top_n` 截断 + tie-break（同分按 symbol 升序）；`passed_codes` 之外的标的被排除；`recent_ic_stats` 从库取近 60 日（stub 库）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（R1–R4）。
- [ ] **Step 5: Commit** `feat(screening): L2 cross-sectional ranker with contribution decomposition (T1)`。

### Task 2: builtin 因子包

- [ ] **Step 1: 写失败测试**（`tests/factors/test_builtin.py`）：五个 builtin 表达式全部通过 DSL 解析（默认复杂度上限）；`register_builtins` 在合成强信号面板上 → 至少一个 active 且 `category="builtin"`、`source="builtin"`；**同闸约束**（spec 场景）：构造 ICIR=0.12 的 builtin（如反转因子在无反转结构面板上）→ 被拒于闸②，阈值与 LLM 因子一致；幂等重跑不重复入库。
- [ ] **Step 2–4: 红 → 实现 → 绿**（R5）。
- [ ] **Step 5: Commit** `feat(factor-lib): builtin manual factor baseline package (T2)`。

### Task 3: 验证与收口

- [ ] 脚本 `tests/validation/2026-10-13-add-screening-ranker-acceptance.py`：合成面板（固定种子，含真实预测结构）+ 注册 builtin + 打分；取 3 个交易日各 Top-100；**抽 10 只候选手算综合分**（脚本内用独立算术：直接从面板取因子值、手写 z-score 与加权公式，不经 ranker 模块），逐条对照 ranker 输出；落 JSON + 报告。
  - 诚实注记：live 2026-09 数据受东财 IP 封禁（issue #325）不可得 → 合成面板替代；手算用独立实现（非复用被测函数）以保证据独立。
- [ ] `uv run pytest tests/screening tests/factors -q` + 全量 `tests/ -m "not live"`（记录，既有 env flake 归因）；ruff/mypy。
- [ ] 报告 + `tasks.md` 全勾 →（控制器 archive）。Commit `docs(openspec): add-screening-ranker validation report + tasks 回填`。
