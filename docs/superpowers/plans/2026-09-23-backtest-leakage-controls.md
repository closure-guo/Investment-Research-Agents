# add-backtest-leakage-controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给回测读数腿装上效度护栏：结算/回测取数统一后复权（as-of 保真）、正式批须过「干净窗口」判定 + 知识泄漏探针披露（超阈降级「上界证据」句式）、批次绑定预登记、报告带生命周期 status 头并纳入索引扫描。

**Architecture:** `akshare_client.fetch_kline` 加 `adjust` 参数（**默认 qfq 不动**，仅结算/回测路径显式传 `hfq`）；新模块 `evals/backtest/leakage_probe.py`（三层题裸问 + 真值）+ `evals/backtest/report.py`（最小 md 渲染 + status 头）；`run_backtest` 增批次类型/窗口校验/预登记绑定/四段披露段。

**Tech Stack:** Python（akshare hfq / `complete_text` 最小 LLM 封装 / sqlite 无关），pytest（fake client / fake llm）。

## Global Constraints

- **工作区**：`.worktrees/outcome-eval`；依赖 Δ1（caliber 常量、预登记机制、`evals/outcome/report.py::assert_outcome_report`）与 Δ2（判定同源、派生入场价）已落地。
- **复权口径（spec）**：结算与回测的区间收益计算 SHALL 用**后复权（hfq）**；**分析输入路径（`nodes/fetch.py` → 管线 kline）保持 qfq 不变**（改它会移动分析师行为基线，属独立变更）。`fetch_kline` 新参数**默认必须仍是 `"qfq"`**（向后兼容硬约束）。
- **口径混用禁令**：结算/回测路径不得在 hfq 与 qfq 序列间混用；`replay` 的快照兜底 K 线（qfq）在缺 `full_kline` 时 SHALL 显式失败/标注，不得静默混口径。
- **探针阈值**：`evals/outcome/caliber.py::LEAKAGE_PROBE_THRESHOLD = 0.60`（预登记默认）；超阈 → 降级句式「泄漏污染下的上界证据（真实 skill ≤ 读数）」。
- **预登记绑定**：回测腿调用 `assert_preregistered(Path("evals/ablation/preregister"), name_contains="outcome", required_fields=OUTCOME_REQUIRED_FIELDS)`（目录多实验共用，**必须**传 `name_contains`）。
- **status 契约**：报告 md 头部 `**status**: active | superseded-by: <path>`；校验用 `evals.causal_ablation.report_status.assert_status_valid` + `evals/outcome/report.py::assert_outcome_report`；`docs/evals/README.md` 扫描目录元组加 `evals/backtest/results`。
- **命令**：`uv run pytest tests/evals/backtest tests/data -v`；`ruff check evals/ src/ tests/ scripts/`；`ruff format --check tests/ evals/`。
- **commit 风格**：中文 + conventional 前缀（`fix(data):` / `feat(evals):` / `docs(evals):`）。

---

### Task 1: 复权口径统一（hfq）+ 路径切换 + 切点登记

**Files:**
- Modify: `src/finance_agent/data/akshare_client.py`（`fetch_kline` 加 `adjust` 参数与三处透传）
- Modify: `src/finance_agent/outcome/track_record/job.py`、`src/finance_agent/outcome/track_record/marking.py`、`src/finance_agent/outcome/job.py`、`evals/backtest/run_backtest.py`（显式 `adjust="hfq"`）
- Test: `tests/data/test_akshare_client.py`（更新：原 `== "qfq"` 断言改为默认值断言 + 新增 hfq 透传断言）、`tests/outcome/test_track_record_job.py`（fake client 断言收到 hfq）
- Modify: `docs/evals/metrics.md`（§2 切点行）

**Interfaces:**
- Produces: `fetch_kline(self, stock_code: str, days: int = 250, *, adjust: str = "qfq") -> pd.DataFrame`；`SETTLEMENT_ADJUST = "hfq"` 常量（放 `akshare_client.py` 顶部或 `track_record/judgment.py`——**择一**，报告注明）

- [ ] **Step 1: 失败测试**

```python
def test_fetch_kline_default_adjust_is_qfq(...):      # 既有行为不变（默认参数）
def test_fetch_kline_hfq_passthrough(...):            # 显式 adjust="hfq" → 三源 kwargs 收到 "hfq"
def test_settlement_job_requests_hfq(tmp_path):        # fake client 记录 adjust → 断言 "hfq"
```

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**

`fetch_kline`：签名加 `*, adjust: str = "qfq"`，三处（`:450`/`:459`/`:477`）由字面量改为 `adjust=adjust`；docstring 更新（默认前复权；结算/回测传后复权，理由=as-of 保真）。
调用方显式传参：`track_record/job.py`（`:86`、`:122`、`:68` 基准不传——指数无复权）、`marking.py`（`:97` 及 `:83`）、`outcome/job.py`（`:112`/`:121`，旧链路保留亦可但建议统一）、`evals/backtest/run_backtest.py`（`:221`）。
`replay.py`：**HFQ 口径护栏已由 Δ2 实现**（`evals/backtest/replay.py:47-59`：`full_kline is None` 且快照 kline 非空 → 抛 `ValueError`，含「hfq 全量 K 线」字样；空帧走优雅无结算）。本任务**不重复实现**——核对护栏语义与本节口径一致（不一致则对齐），并把 `SETTLEMENT_ADJUST` 常量落到 `akshare_client.py` 顶部（与护栏文案呼应）。

- [ ] **Step 4: 绿 + 切点行 + Commit**

`metrics.md` §2 追加：「**复权口径切点（2026-09-23，delta add-backtest-leakage-controls，未跑批）**：结算与回测取数统一后复权（hfq），分析输入保持前复权；**跨切点的结算数值（分红除权样本）不可直接比较**，存量已结算行不重算。」

```bash
git commit -m "fix(data): 结算/回测取数统一后复权（fetch_kline 加 adjust 参数，默认仍 qfq）（delta add-backtest-leakage-controls）"
```

---

### Task 2: 知识泄漏探针模块

**Files:**
- Create: `evals/backtest/leakage_probe.py`
- Test: `tests/evals/backtest/test_leakage_probe.py`

**Interfaces:**
- Consumes: `finance_agent.llm.gateway.complete_text`；`AKShareClient.fetch_kline(adjust="hfq")` / `fetch_news`；`evals/outcome/caliber.py::LEAKAGE_PROBE_THRESHOLD`；`finance_agent.nodes._llm_utils.parse_json_response`
- Produces: `run_leakage_probe(codes: list[str], decision_date: str, *, window_days: int = 20, n_tickers: int = 10, seed: int = 42, client=None, llm: Callable[[str], str] | None = None) -> dict`（`{"probe_n", "questions_per_ticker", "direction_hit_rate", "magnitude_hit_rate", "event_hit_rate", "unknown_ratio", "threshold", "downgraded", "details": [...]}`）

- [ ] **Step 1: 失败测试**（fake `llm` 返回构造 JSON；fake client 返回构造 K 线/新闻）
  - 全答对 → `direction_hit_rate == 1.0` 且 `downgraded is True`（> 0.60）。
  - 全答错 → `0.0`、`downgraded is False`。
  - 拒答/不可解析 50% → `unknown_ratio == 0.5`，且未知**不计入**答题分母（`direction_hit_rate` 只基于可解析样本；分母为 0 时返回 `None`，不报 0）。
  - 事件题真值不可得（`fetch_news` 返回空）→ 计入 unknown（不按答错）。

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**

```python
_DIRECTION_PROMPT = """回答一个事实性问题，不要解释：{ticker}（{name}）在 {start} 至 {end} 区间内，收盘价相对沪深300是上涨还是下跌？只回答 JSON：{{"direction": "up"|"down"}}"""
_MAGNITUDE_PROMPT = ...  # {"bucket": "大涨|小涨|持平|小跌|大跌"}（±2% 同源分桶）
_EVENT_PROMPT = ...      # {"event": "有"|"无"|"不确定"}
```
`_truth_direction/_truth_magnitude` 由 hfq K 线相对基准算得（与结算口径一致）；`_truth_event` 由 `fetch_news(ticker)` 标题关键词 + 区间起止的涨跌幅度代理（不可得 → unknown）。
`direction_hit_rate = hits / parsed`（parsed==0 → None）；`unknown_ratio = unknown / total_questions`；`downgraded = rate is not None and rate > threshold`。
模块 docstring 说明：裸问条件（不带 as-of 快照）、探针是实证披露而非净化假设。

- [ ] **Step 4: 绿 + Commit**

```bash
git commit -m "feat(evals): 知识泄漏探针——三层题裸问 + 真值命中率与未知占比（delta add-backtest-leakage-controls）"
```

---

### Task 3: 干净窗口校验 + 预登记绑定 + 报告四段披露

**Files:**
- Create: `evals/backtest/report.py`（四段组装 + md 渲染，见 Task 4）
- Modify: `evals/backtest/run_backtest.py`
- Test: `tests/evals/backtest/test_run_backtest.py`（追加）、`tests/evals/backtest/test_clean_window.py`

**Interfaces:**
- Produces: `assert_clean_window(decision_dates: list[str], *, as_of: str, window_days: int = 20) -> dict`（`{"passed": bool, "reason": str}`——全部决策日距 as_of ≥ window_days 交易日且探针读数可得）；`build_disclosures(*, preregistration, clean_window, probe, regime_covered, regime_limited) -> dict`；`run_backtest(..., batch_kind="pathway"|"formal", disclosures=None)` 报告增 `batch_kind / preregister / clean_window / leakage_probe / regime_coverage` 五键
- CLI 增：`--batch-kind {pathway,formal}`（默认 `pathway`）、`--as-of`（默认今天）、`--probe/--no-probe`（formal 强制 probe）

- [ ] **Step 1: 失败测试**
  - `assert_clean_window`：决策日足够早 → passed；有任一决策日不足窗口 → `passed False` + reason 含该日期。
  - `--batch-kind formal` 且无有效预登记 → 抛 `MissingPreregistrationError`（不落报告）；预登记有效但探针超阈 → 报告 `leakage_probe.downgraded True` 且 `conclusion` 采用「上界证据」句式（断言句含「上界证据」且不含无条件「赚钱能力主张成立」）。
  - `pathway` 批 → 报告 `batch_kind=="pathway"`、结论段标注「通路验证」且**无** skill 结论句（断言不含「显著为正/为负」）。

- [ ] **Step 2: 红**

- [ ] **Step 3: 实现**

`run_backtest.py`：`main()` 增加批次类型与校验；`run_backtest()` 的 return dict 增五键（四段披露 + batch_kind），`methodology` 段补 hfq 说明（Task 1 后）。
导入：`from evals.causal_ablation.preregister import OUTCOME_REQUIRED_FIELDS, assert_preregistered`。

- [ ] **Step 4: 绿 + Commit**

```bash
git commit -m "feat(evals): 回测干净窗口判定 + 预登记绑定 + 探针/regime 披露段（delta add-backtest-leakage-controls）"
```

---

### Task 4: 回测报告 md 渲染 + status 头 + 索引扫描扩展 + pilot 标注

**Files:**
- Modify: `evals/backtest/report.py`（md 渲染）
- Modify: `evals/backtest/run_backtest.py`（落 `evals/backtest/results/<name>.md`）
- Modify: `docs/evals/README.md`（扫描目录元组加 `evals/backtest/results`）
- Modify: `evals/backtest/results/pilot-2023-shock.md`（就地补 status 头 + 「通路验证 + 泄漏风险」标注，原文与数字保留）
- Test: `tests/evals/test_report_status.py`（参数化目录扩至回测 results）、`tests/evals/backtest/test_report_render.py`

**Interfaces:**
- Produces: `render_backtest_report_md(report: dict, *, name: str) -> str`（头部含 `**status**: active` + 日期 + 批次类型 + 预登记指针 + 探针读数 + 干净窗口判定 + regime 覆盖 + 四指标表 + 基线对照 + 结论句式；渲染后经 `evals.outcome.report.assert_outcome_report(text)` 自校）

- [ ] **Step 1: 失败测试**
  - 渲染出的 md 头能被 `assert_outcome_report` 通过；缺 status 头时该断言抛错。
  - `tests/evals/test_report_status.py` 的参数化目录集合包含 `evals/backtest/results`，且该目录下每个 md 的 status 合法（pilot 补头后通过）。
  - pilot 文件含「通路验证」与「泄漏风险」字样且原有数字/段落未被删改（断言若干原文片段仍在）。

- [ ] **Step 2: 红 → Step 3: 实现 → Step 4: 绿 + Commit**

```bash
git commit -m "feat(evals): 回测报告 md 渲染与生命周期 status 头 + 索引扫描扩展 + pilot 定位标注（delta add-backtest-leakage-controls）"
```

---

### Task 5: 验证收口

**Files:**
- Create: `tests/validation/2026-09-23-add-backtest-leakage-controls-validation.md`
- Modify: `openspec/changes/add-backtest-leakage-controls/tasks.md`（勾选）、`metrics.md` §2（Task 1 已加复权切点；此处补「泄漏控制启用」切点行）

- [ ] **Step 1: 全量相关测试 + lint**

```bash
uv run pytest tests/evals/backtest tests/data tests/outcome -v
uv run ruff check evals/ src/ tests/ && uv run ruff format --check tests/ evals/
```

- [ ] **Step 2: 离线通路验证批实跑（零/少量 LLM）**
  - 用**假 llm**（离线）跑 `leakage_probe` 全流程（真值来自 akshare 或构造）；
  - `--batch-kind pathway` 小样本跑通（fake replay_fn）→ 报告落 md、status 头可解析、结论为通路验证、五键齐；
  - `--batch-kind formal`（无预登记）→ 拒绝；补一份临时预登记（tmp_path）→ 通过校验并输出 `downgraded` 句式。
  - 产出与日志摘要落验证报告。

- [ ] **Step 3: 台账与勾选**

`metrics.md` §2 追加：「**泄漏控制启用切点（2026-09-23，delta add-backtest-leakage-controls，未跑批）**：回测正式批绑定预登记 + 干净窗口判定 + 探针披露（阈值 0.60，超阈降级「上界证据」）；深历史批次永久定位通路验证；`evals/backtest/results/*.md` 纳入结论注册表索引。」`tasks.md` 勾选。

- [ ] **Step 4: 人工验证报告 + Commit**

报告含：验收项对照表（六条 delta 需求 → 落点）、离线实跑输出摘要、**owner 待办**（真实探针需 LLM 预算；正式批窗口与样本量由预登记 MDE 反算后 owner 批预算）、异常记录。

```bash
openspec validate add-backtest-leakage-controls --strict
git commit -m "test(evals): add-backtest-leakage-controls 验证收口——离线通路跑批 + 台账登记（tasks 勾选）"
```

---

## Self-Review（计划自审）

- **Spec 覆盖**：历史离线回放（干净窗口 + 结算同源，T1/T3）/ 绩效与基线（复权统一，T1）/ 分层抽样（干净窗口冲突条款 → `regime_coverage` 披露 + 结论限定，T3）/ 知识泄漏探针（T2+T3）/ 批次预登记与报告生命周期（T3+T4）——五条需求均有落点。
- **占位符扫描**：探针三题模板以 `...` 给出实现位（真值构造在 Step 3 明确到函数级），K 线列名/事件真值代理明确「不可得 → unknown」；无 TBD。
- **类型一致性**：`run_leakage_probe` 返回键在 T2 测试与 T3 报告段一致；`assert_clean_window` 返回 `{passed, reason}` 在 T3 测试与实现一致；`render_backtest_report_md` 在 T4 测试与 T3 的 disclosures 组装之间通过 `report` dict 键名对齐（五键名固定）。
- **已知边界**：`fetch_index_kline` 无复权概念（指数）——不需要改动；`evals/golden/gates.py` 保留自有 20 日语义（Δ2 design 已登记，本 delta 不动）。
