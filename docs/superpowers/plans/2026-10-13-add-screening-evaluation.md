# add-screening-evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 候选池前向命中率跟踪（5/10/20 日，入选组 vs 落选组，撮合口径与时点截断纪律）、选股消融三变体（因子打分/固定种子随机/等权指数，配对 bootstrap B=10000）、±L3 粗筛变体（keep/drop 前向收益 + 踩雷率）、因子来源分层考核（builtin vs LLM）。

**Architecture:** 新包 `evals/screening/`：`forward.py`（前向收益 + 分层统计，消费 screening 库 + snapshot 面板 + matching 引擎入场口径）；`ablation.py`（三变体 + 配对 bootstrap + CI 含 0 措辞守卫）；`l3.py`（±L3 keep/drop 对照）；`stratify.py`（因子来源分层）。全部纯计算 + 注入数据，测试用合成面板（固定种子），零网络。

**Tech Stack:** Python 3.14 / pandas / numpy（bootstrap）/ pytest；复用 `execution/matching`、`screening/{store,ranker,pipeline}`、`factors/{eval_engine,library}`。

## Global Constraints

- 测试：`uv run pytest tests/evals/screening -q`（新目录）+ 全量；ruff/mypy 同仓库纪律；TDD 红→绿；既有测试零修改。
- 口径（spec 明文）：前向收益用**与决策回放相同的时点截断纪律与撮合口径**（入场走 `match_order`：D+1 开盘 ± 滑点、一字涨停不成交记原因码且剔除；出场 = horizon 日收盘；任何统计只用 ≤ as_of 数据）；报告披露有效 n 与适用口径。
- CI 含 0 措辞：只写「无显著差异」，禁止「无效/无价值」类表述（模板断言）。
- 随机变体固定种子（spec 场景：种子 42 两次执行标的序列完全一致）。
- 配对 bootstrap B=10000。
- 报告落 `docs/evals/`（样本报告 + 上线后每 20 交易日一次的 cadence 作为后续项，挂 issue——live 数据受 #325 阻塞）。

## 关键设计决策

**V1 — 前向收益**：`forward_pool_returns(*, pool_dates: Sequence[str], db_path=None, panel: MarketPanel, horizons=(5,10,20), config=None) -> dict`：对每个池日：入选组 = `selection_pool` 该日 symbols；落选组 = 该日 `l2_candidates` 中不在池内的 symbols；每组逐标的算 `entry/exit` 收益（入场：池日次一交易日 `match_order` 口径；出场：入场日后第 horizon 个交易日收盘/入场价 − 1）；未成交剔除并计 `unfilled_counts`；输出每组每 horizon 的 `{n, mean, win_rate, ci95}`（配对 bootstrap：对同池日配对样本重采样）。

**V2 — 三变体**：`random_selection(pool_dates, universe, *, seed=42, top_n=100)`（`random.Random(seed)` 逐日抽样，确定性）；等权指数基线 = 全市场等权组合（该时段所有 L2 名单标的等权）的同期收益；三变体共享时段与 horizon；`paired_bootstrap_ci(a, b, B=10000, seed=42)`。

**V3 — ±L3**：`l3_keep_drop_compare(pool_dates, db_path, panel, horizon=20)`：keep 组 = `triage_decisions.verdict=="keep"`（且入池）；drop 组 = verdict=="drop"；输出两组 `{n, mean_20d, 踩雷率}` + 配对 CI；措辞判定：CI 不含 0 且 drop < keep → 「具有排雷价值」；否则「无显著差异」。

**V4 — 分层**：`stratify_by_factor_source(...)`：用 builtin-only / LLM-only 因子子集重跑 `rank_universe`（同 IC 统计）→ 两分层各自 Top-N 的前向收益对照。

**V5 — 报告**：`evals/screening/report.py::render_screening_report_md(payload) -> str`（markdown：分组表 + CI + 有效 n + 口径披露 + 措辞守卫）；样本报告落 `docs/evals/2026-10-13-screening-forward-sample.md`（合成数据，明示）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `evals/screening/__init__.py`、`forward.py`、`ablation.py`、`l3.py`、`stratify.py`、`report.py`（新） | 全部计算与报告 |
| `tests/evals/screening/test_forward.py`、`test_ablation.py`、`test_l3.py`、`test_stratify.py`、`test_report.py`（新） | 单测 |
| `tests/validation/2026-10-13-add-screening-evaluation-*.py/.md` + `docs/evals/2026-10-13-screening-forward-sample.md` | 验证与样本报告 |

---

### Task 1: 前向收益 + 分层统计
- [ ] 测试：手算小面板（3 池日 × 2 horizon）逐数对照；未成交剔除 + 原因计数；时点截断（只用 ≤ as_of）；入选 vs 落选分组；CI 计算（配对 bootstrap 种子固定、两次一致）。
- [ ] Commit `feat(evals): screening forward-return tracking with matching caliber (T1)`。

### Task 2: 三变体 + 随机可复现
- [ ] 测试：`random_selection` 种子 42 两次一致（spec 场景）；三变体同窗口；`paired_bootstrap_ci` 已知分布手算对照；CI 含 0 措辞守卫（报告渲染断言「无显著差异」且无「无效」）。
- [ ] Commit `feat(evals): screening ablation variants with paired bootstrap (T2)`。

### Task 3: ±L3 + 分层 + 报告
- [ ] 测试：keep/drop 对照（spec 场景 +2.1%/-3.4% CI 不含 0 → 「具有排雷价值」+ 窗口与 n 披露）；反向（CI 含 0 → 无显著差异）；分层（builtin vs LLM 两行）；报告渲染含有效 n 与口径披露。
- [ ] Commit `feat(evals): L3 keep/drop and factor-source stratification (T3)`。

### Task 4: 验证收口
- [ ] 合成数据端到端：30 池日合成历史 → 全链路（forward/三变体/±L3/分层/报告）→ JSON + 样本报告落 `docs/evals/2026-10-13-screening-forward-sample.md`（明示合成）。
- [ ] live cadence（每 20 交易日）与真实数据报告挂 issue（#325 相关）；验证报告如实记录边界。
- [ ] `uv run pytest tests/evals/screening -q` + 全量 + ruff/mypy；`tasks.md` 全勾（4.2 如实标注）→（控制器 archive）。
