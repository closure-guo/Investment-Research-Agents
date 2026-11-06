# 零买入第二轮诊断（P0：合成完美案例三岔口）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用合成完备案例（完美/缺陷）在 3 个配置下各跑一次全管线，把「0 买入」精确定位到「提示词否决层 / 图内拦截层（FM/校验/打回）/ 数据层」，产出一份有明确后继动作的三岔口裁决；**本计划不修改任何生产提示词**。

**Architecture:** 复用 `evals/ablation.py` 的 `build_variant_graph("full")`（五层管线实验副本，语义与主图一致）直接吃手工态数据：以 `fetch._stub_fetch_data` 的键集为底（与真实 fetch 输出同构、有 `TestStubRealKeysetParity` 守卫）替换出完美/缺陷两套自洽数据 → 补 `compute_metrics` → 图调用 → 按层 dump 最终 state。变体注入沿用第一轮的模块属性 patch（`scripts/run_zero_buy_ablation.py` 的 `patch_prompts`），本轮扩到分析师模块。

**Tech Stack:** Python 3.12 / LangGraph（`build_variant_graph("full")`）/ pandas / pytest / Langfuse（trace 元数据核验注入）。

## Global Constraints

- **提示词权威源**：`src/finance_agent/prompts/*.md`；**变体提示词一律放 `evals/variants/round2/`**（不得进生产目录，否则会被 `deploy_prompts.py` 当生产发布）。
- **不改生产提示词、不改生产代码**；唯一例外：`scripts/run_zero_buy_ablation.py` 的 `_NODE_MODULES` 追加 `finance_agent.nodes.analysts`（实验脚本，非生产路径）。
- **实验进程零副作用**：本计划全部运行为宿主进程内图调用——不派单、不写 `data/sessions.db`、不产生 `predictions` 行、不重启任何服务。跑前跑后各查一次生产库行数应不变（`predictions`=287、`sessions`=586，基线取自 2026-11-06）。
- **合成数据自洽**：三表严格满足 资产=负债+权益、净利润=利润总额−所得税、经营+投资+筹资=现金净变动；K 线收盘价与 `stock_quote.price` 一致；所有日期 ≤ `decision_date`。
- **成本闸门**：P0 全部运行 ≤ 100 万 tokens（3 次全管线 ≈ 75 万实测口径）；超则停。
- **停止点**：Task 4 报告写完即停——P1（四态 schema delta）等 owner 拍板后单独立项，不得顺手改生产。
- **币种/单位**：财报数值单位沿用 stub 惯例（百万级抽象单位，不引入"亿元"换算）。

## 背景与判据（执行者必读）

第一轮消融结论（`docs/evals/zero-buy-ablation-2026-11-06.md`）：生产提示词 11 个中 7 个带锁（trader v37 / risk_judge v35 / fund_manager v32 / 4 分析师 v31/v32/v36，Langfuse production 实发版本核验）；两臂删锁后 0 buy；B 臂模型在变体删净「20 个交易日」文本后仍引用窗口逻辑（来源=上游材料）。判据悬置在「锁在提示词」与「锁在行为/模型」之间。

**三岔口判据（预先固定，不许事后改写）：**

| P0 结果 | 裁决 | 后继动作 |
|---|---|---|
| 生产提示词+完美案例 **不买**；B' 变体+完美案例 **买** | 锁在提示词层 | 进入 P1/P2：按 B' 清单改生产提示词（走完整 OpenSpec 管线） |
| B' 变体+完美案例 **仍不买** | 锁在图内拦截层或行为层 | 用层 dump 定位（FM 审批 / 价位校验 / 打回环 / confidence 档位），转入 P1 的 schema+拦截修复 |
| B' 变体+缺陷案例 **买了** | 硬否决缺失 | 回归门禁第 3 用例（必回避）不成立——先补致命否决白名单，再谈其他 |

## 文件结构

| 文件 | 职责 |
|---|---|
| `evals/synthetic_case.py`（新建） | 完美/缺陷两套合成数据（基于 `_stub_fetch_data` 键集） |
| `tests/evals/test_synthetic_case.py`（新建） | fixture 自洽性单测 |
| `evals/variants/round2/trader_bprime.md`（新建） | 删周期契约 + 置换买入依据 + 「满足条件必须输出 buy」 |
| `evals/variants/round2/risk_judge_bprime.md`（新建） | 删契约 + 滞回收窄（仅约束翻转）+ 否决改降仓 |
| `evals/variants/round2/fund_manager_bprime.md`（新建） | 删契约（审批逻辑保留） |
| `evals/variants/round2/{fundamental,technical,macro,sentiment}_analyst_bprime.md`（新建） | 删 horizon_hint 的「T+20 考核/禁作主因」括注 |
| `scripts/run_synthetic_case.py`（新建） | 合成案例运行器（变体注入 + 图调用 + 分层 dump） |
| `tests/scripts/test_run_synthetic_case.py`（新建） | 运行器单测（mock 图，不真跑 LLM） |
| `scripts/run_zero_buy_ablation.py`（改） | `_NODE_MODULES` 追加 analysts 模块（patch 覆盖分析师提示词） |
| `docs/evals/zero-buy-round2-p0-2026-11-06.md`（新建） | 三岔口报告 |

---

### Task 1: 合成 fixture 模块（完美/缺陷）

**Files:**
- Create: `evals/synthetic_case.py`
- Test: `tests/evals/test_synthetic_case.py`

**Interfaces:**
- Produces: `SYN_CODE = "999999.SH"`、`SYN_NAME = "合成案例"`
- Produces: `build_synthetic_state(case: str, *, decision_date: str = "2026-10-30") -> dict`——`case` ∈ {`"perfect"`, `"defect"`}，其余值抛 `ValueError`。返回键集 = `_stub_fetch_data` 输出键集 ∪ `{stock_code, stock_name}`。
- Task 3 消费上述符号。

- [ ] **Step 1: 写失败测试**

```python
# tests/evals/test_synthetic_case.py
"""合成案例 fixture 单测（2026-11-06 零买入第二轮 P0）。

口径：数据键集必须与 TESTING stub 同构（下游节点按真实键集消费）；
三表会计恒等式严格成立；K 线为确定性上升趋势且日期不越界。
"""

from __future__ import annotations

import pytest

from evals.synthetic_case import SYN_CODE, SYN_NAME, build_synthetic_state
from finance_agent.nodes.fetch import _stub_fetch_data


def test_key_parity_with_stub():
    stub = _stub_fetch_data({"stock_code": "X", "stock_name": "X"})
    state = build_synthetic_state("perfect")
    assert set(state) == set(stub) | {"stock_code", "stock_name"}
    assert state["stock_code"] == SYN_CODE
    assert state["stock_name"] == SYN_NAME


def test_perfect_case_financials_are_sound():
    s = build_synthetic_state("perfect")
    bs = s["balance_sheet"].iloc[0]
    assert bs["资产总计"] == bs["负债合计"] + bs["所有者权益(或股东权益)合计"]
    assert bs["负债合计"] / bs["资产总计"] < 0.35  # 低负债
    inc = s["income_statement"].iloc[0]
    assert inc["净利润"] == inc["利润总额"] - inc["所得税费用"]
    assert inc["归母净利润"] > 0
    cf = s["cash_flow_statement"].iloc[0]
    assert cf["经营活动产生的现金流量净额"] > inc["归母净利润"]  # 现金流覆盖利润
    cf_prev = s["cash_flow_statement"].iloc[1]
    assert (
        cf["经营活动产生的现金流量净额"]
        + cf["投资活动产生的现金流量净额"]
        + cf["筹资活动产生的现金流量净额"]
        == cf["现金及现金等价物净增加额"]
    )
    assert cf_prev is not None


def test_perfect_kline_uptrend_bounded_and_quote_consistent():
    s = build_synthetic_state("perfect")
    k = s["kline"]
    assert len(k) == 300
    assert k["日期"].max() <= "2026-10-30"
    assert k["收盘"].iloc[-1] > k["收盘"].iloc[:60].mean() * 1.3  # 明显上行
    assert (k["最高"] >= k["收盘"]).all() and (k["最低"] <= k["收盘"]).all()
    assert s["stock_quote"]["price"] == float(k["收盘"].iloc[-1])


def test_defect_case_is_insolvent_and_illiquid():
    d = build_synthetic_state("defect")
    bs = d["balance_sheet"].iloc[0]
    assert bs["负债合计"] > bs["资产总计"]  # 资不抵债
    assert d["kline"]["成交量"].iloc[-1] <= 100  # 成交枯竭
    assert d["cash_flow_statement"].iloc[0]["经营活动产生的现金流量净额"] < 0


def test_no_catalyst_events():
    for case in ("perfect", "defect"):
        s = build_synthetic_state(case)
        assert s["news_list"] == [] and s["announcements"] == []
        assert s["key_events"] == []


def test_unknown_case_raises():
    with pytest.raises(ValueError):
        build_synthetic_state("nope")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evals/test_synthetic_case.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.synthetic_case'`

- [ ] **Step 3: 最小实现**

```python
# evals/synthetic_case.py
"""合成案例 fixture（2026-11-06 零买入第二轮 P0）。

数据完全离线：以 ``finance_agent.nodes.fetch._stub_fetch_data`` 的键集为底
（与真实 fetch_data 输出同构，键集由 TestStubRealKeysetParity 守卫），仅替换数值。
两套数据自洽：

- perfect：低负债（25%）、现金流覆盖利润（经营现金流/归母净利 > 1）、
  三年营收利润双增、300 日温和上升趋势 + 近期窄幅整理、成交活跃、
  无新闻/公告/事件（**没有日历催化剂——这正是要测的真空期场景**）；
- defect：完美数据 + 资不抵债（负债 1100 > 资产 1000）+ 成交枯竭（量 50）
  + 经营现金流为负 —— 回归门禁「必回避」用例的底稿。

私有函数 ``_stub_fetch_data`` 仅在实验代码内引用；其键集有专门守卫测试，
若上游增删键本模块测试会先红。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from finance_agent.nodes.fetch import _stub_fetch_data

SYN_CODE = "999999.SH"
SYN_NAME = "合成案例"
_DECISION_DATE = "2026-10-30"


def _kline(rows: int = 300, *, end: str = _DECISION_DATE, illiquid: bool = False) -> pd.DataFrame:
    """确定性 K 线：300 日线性上升（12→19.5 附近），末 20 日窄幅整理。

    量列：活跃 8000±200；illiquid=True 时末 30 日量降至 50（枯竭）。
    """
    dates = pd.bdate_range(end=end, periods=rows).strftime("%Y-%m-%d")
    closes = []
    for i in range(rows):
        base = 12.0 + i * 0.025  # 12.0 → 19.475
        closes.append(round(base, 2))
    # 末 20 日横盘整理（-1.5% 区间内波动，确定性锯齿）
    for j in range(20):
        idx = rows - 20 + j
        closes[idx] = round(closes[rows - 21] * (1 + (0.01 if j % 2 else -0.005)), 2)
    opens = [closes[0]] + closes[:-1]
    highs = [round(max(o, c) * 1.008, 2) for o, c in zip(opens, closes)]
    lows = [round(min(o, c) * 0.992, 2) for o, c in zip(opens, closes)]
    volumes = [8000.0 + (i % 5) * 200 for i in range(rows)]
    if illiquid:
        for idx in range(rows - 30, rows):
            volumes[idx] = 50.0
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": opens,
            "收盘": closes,
            "最高": highs,
            "最低": lows,
            "成交量": volumes,
        }
    )


def _balance_sheet(*, insolvent: bool) -> pd.DataFrame:
    """两年资产负债表，资产=负债+权益 严格成立。"""
    if insolvent:  # 负债 1100 > 资产 1000，权益 -100
        liabilities, equity = 1100.0, -100.0
    else:  # 负债 250 / 资产 1000 = 25%
        liabilities, equity = 250.0, 750.0
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231"],
            "资产总计": [1000.0, 900.0],
            "负债合计": [liabilities, 270.0],
            "所有者权益(或股东权益)合计": [equity, 630.0],
            "归母所有者权益": [equity, 630.0],
            "流动资产合计": [600.0, 540.0],
            "流动负债合计": [150.0, 135.0],
            "存货": [60.0, 54.0],
            "货币资金": [400.0, 360.0],
            "短期借款": [0.0, 0.0],
            "长期借款": [0.0, 0.0],
            "应付债券": [0.0, 0.0],
            "一年内到期的非流动负债": [0.0, 0.0],
            "累计折旧": [40.0, 36.0],
            "未分配利润": [500.0, 450.0],
        }
    )


def _income_statement() -> pd.DataFrame:
    """三年利润表，净利润=利润总额−所得税 严格成立；毛利率 45%/43%/41%。"""
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231", "20231231"],
            "营业收入": [1200.0, 1000.0, 850.0],
            "营业成本": [660.0, 570.0, 501.5],
            "利润总额": [420.0, 350.0, 297.5],
            "所得税费用": [105.0, 87.5, 74.4],
            "净利润": [315.0, 262.5, 223.1],
            "归母净利润": [315.0, 262.5, 223.1],
            "利息费用": [2.0, 2.0, 2.0],
        }
    )


def _cash_flow(*, negative_operating: bool) -> pd.DataFrame:
    """两年现金流量表，经营+投资+筹资=净增加额 严格成立。"""
    operating = -200.0 if negative_operating else 350.0
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231"],
            "经营活动产生的现金流量净额": [operating, 290.0],
            "投资活动产生的现金流量净额": [-80.0, -70.0],
            "筹资活动产生的现金流量净额": [-30.0, -25.0],
            "现金及现金等价物净增加额": [operating - 110.0, 195.0],
            "分配股利、利润或偿付利息所支付的现金": [20.0, 18.0],
        }
    )


def _latest_period_snapshot() -> dict[str, Any]:
    """与三表一致的最近报告期快照（毛利率/负债率/同比）。"""
    return {
        "报告日": "2025-12-31",
        "期类型": "年报",
        "营业总收入(累计)": 1200.0,
        "归母净利润(累计)": 315.0,
        "营业成本(累计)": 660.0,
        "毛利率(%)": 45.0,
        "资产负债率(%)": 25.0,
        "存货": 60.0,
        "合同负债": 80.0,
        "上年同期营业总收入": 1000.0,
        "上年同期归母净利润": 262.5,
        "营收同比(%)": 20.0,
        "归母净利同比(%)": 20.0,
        "missing": [],
    }


def build_synthetic_state(case: str, *, decision_date: str = _DECISION_DATE) -> dict[str, Any]:
    """构造合成案例的 fetch_data 同构状态。

    Args:
        case: ``"perfect"``（完美案例）或 ``"defect"``（资不抵债+枯竭）。
        decision_date: K 线截止日（须为交易日字符串）。

    Returns:
        键集 = ``_stub_fetch_data`` 输出 ∪ ``{stock_code, stock_name}``。
    """
    if case not in ("perfect", "defect"):
        raise ValueError(f"未知合成案例: {case!r}（仅支持 perfect/defect）")
    defect = case == "defect"
    kline = _kline(end=decision_date, illiquid=defect)
    state: dict[str, Any] = {
        "stock_code": SYN_CODE,
        "stock_name": SYN_NAME,
        **_stub_fetch_data({"stock_code": SYN_CODE, "stock_name": SYN_NAME}),
        "balance_sheet": _balance_sheet(insolvent=defect),
        "income_statement": _income_statement(),
        "cash_flow_statement": _cash_flow(negative_operating=defect),
        "financial_indicators": None,
        "industry_info": {"industry": "电子元件", "name": SYN_NAME},
        "stock_quote": {
            "price": float(kline["收盘"].iloc[-1]),
            "name": SYN_NAME,
            "code": SYN_CODE,
        },
        "kline": kline,
        "benchmark_kline": _kline(end=decision_date),
        "industry_pe": None,
        "quarterly_income": None,
        "peer_financials": None,
        "latest_period_snapshot": _latest_period_snapshot(),
        "news_list": [],
        "announcements": [],
        "key_events": [],
    }
    return state
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evals/test_synthetic_case.py -v`
Expected: `6 passed`

- [ ] **Step 5: Lint + 提交**

```bash
uv run ruff format evals/synthetic_case.py tests/evals/test_synthetic_case.py
uv run ruff check evals/synthetic_case.py tests/evals/test_synthetic_case.py
uv run mypy evals/synthetic_case.py
git add evals/synthetic_case.py tests/evals/test_synthetic_case.py
git commit -m "feat(eval): 合成案例 fixture——完美/缺陷两套自洽数据（零买入 P0）"
```

---

### Task 2: B' 变体提示词 7 份（含生产版本一致性核对）

**Files:**
- Create: `evals/variants/round2/trader_bprime.md`
- Create: `evals/variants/round2/risk_judge_bprime.md`
- Create: `evals/variants/round2/fund_manager_bprime.md`
- Create: `evals/variants/round2/{fundamental,technical,macro,sentiment}_analyst_bprime.md`

**Interfaces:**
- Produces: 7 个变体文件；Task 3/4 以 `--variant` 逐个注入。文件名前缀决定 patch 目标：`trader_*`→trader、`risk_judge_*`→risk_judge、`fund_manager_*`→fund_manager、`*_analyst_*`→对应分析师（Task 3 扩展映射）。

- [ ] **Step 1: 核对本地基线 == Langfuse production**

对将要修改的 7 个提示词逐一执行（示例 trader，其余替换名字）：

```bash
docker exec -i finance-agent-postgres-1 sh -c 'psql -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-postgres} -At' <<'SQL' > tmp/lf-base.txt
SELECT p.prompt#>>'{text}' FROM prompts p
WHERE p.name='trader' AND p.labels::text LIKE '%production%';
SQL
diff <(sed 's/\r$//' src/finance_agent/prompts/trader.md) <(sed 's/\r$//' tmp/lf-base.txt) && echo SAME
```

Expected: `SAME`。**若有 diff**：以 Langfuse 文本为基线（先落盘到 `src/finance_agent/prompts/<name>.md` 需走 deploy 一致性流程——本轮禁止，故改为在变体文件里以 Langfuse 文本为底、并在报告中记录漂移事实，变体制作后删除 `tmp/lf-base.txt`）。

- [ ] **Step 2: 生成变体（cp + 单点替换，逐字保真）**

```bash
mkdir -p evals/variants/round2
for n in trader risk_judge fund_manager fundamental_analyst technical_analyst macro_analyst sentiment_analyst; do
  cp "src/finance_agent/prompts/$n.md" "evals/variants/round2/${n}_bprime.md"
done
```

**2a. `trader_bprime.md`**：删除整段 `## 周期契约`（标题至 `## 输出格式` 之前），并在 `## 决策语义` 标题下插入两条：

```markdown
## 决策语义

- buy MUST 说明为何在当前价位买入、认错位在哪、仓位多少；不要求证明窗口内催化剂。
- 满足买入条件且无硬否决时必须输出 buy，不得因一般性市场风险额外加码否决（仓位由
  position_size 档位与风险模块承担）；未满足时 MUST 逐条列出具体卡点条件。
```

**2b. `risk_judge_bprime.md`**：删除整段 `## 周期契约`；把「证据均衡与决策滞回」整条替换为：

```markdown
- 决策滞回（仅约束方向翻转）：若 context 含「近窗决策史」，方向翻转（与最近一次方向不同，执行↔观望或 buy↔sell）MUST 在 reasoning 中显式申报触发翻转的增量事实，无增量请维持近窗前向方向。首次建仓（近窗无同向执行）不受「证据均衡带」增量约束——允许以价位逻辑（估值分位、技术形态、止损空间与赔率）为充分依据。系统会复核：未申报增量的翻转将被打回重申一次，重申仍无则维持前判
```

并在 `## 决策语义` 首条之后插入：

```markdown
- 风险辩论对买入方案的否决，只能落到『降低仓位档位』或『放宽/上移止损』；MUST NOT 以
  『止损距离小于日波动』直接否定方向
```

**2c. `fund_manager_bprime.md`**：仅删除整段 `## 周期契约`（审批语义原样保留）。

**2d-2g. 四个分析师变体**：删除 horizon_hint 条款中的括注 `（决策层按 T+20 个交易日考核，long 档论据不能支撑买入主因）`，其余（short/mid/long 档定义、null 纪律、示例）一字不动。原文形如：

```
标注该论据的**预期兑现窗口**——short=≤5 个交易日兑现、mid=6–20 个交易日、long=>20 个交易日（决策层按 T+20 个交易日考核，long 档论据不能支撑买入主因）。无法判断时置 null：
```

替换为：

```
标注该论据的**预期兑现窗口**——short=≤5 个交易日兑现、mid=6–20 个交易日、long=>20 个交易日。无法判断时置 null：
```

- [ ] **Step 3: 校验替换生效**

```bash
# 1) 锁字样清零（应全部无输出，grep 退出码 1）
grep -n "周期契约\|MUST 说明催化剂\|买入主因" evals/variants/round2/trader_bprime.md evals/variants/round2/risk_judge_bprime.md evals/variants/round2/fund_manager_bprime.md
grep -n "long 档论据不能支撑买入主因" evals/variants/round2/*_analyst_bprime.md
# 2) 替换句就位（应各命中 1 行）
grep -n "为何在当前价位买入" evals/variants/round2/trader_bprime.md
grep -n "首次建仓" evals/variants/round2/risk_judge_bprime.md
grep -n "止损距离小于日波动" evals/variants/round2/risk_judge_bprime.md
# 3) 字段保留（每份分析师变体应命中）
grep -c "horizon_hint" evals/variants/round2/*_analyst_bprime.md
# 4) 行数对照：trader/risk_judge/FM 应各比原文件少 ~9 行
wc -l src/finance_agent/prompts/trader.md evals/variants/round2/trader_bprime.md
```

- [ ] **Step 4: 提交**

```bash
git add evals/variants/round2
git commit -m "chore(eval): 零买入 P0 变体——B' 全拆（trader/risk_judge/FM/4 分析师）+ 滞回收窄"
```

---

### Task 3: 合成案例运行器

**Files:**
- Create: `scripts/run_synthetic_case.py`
- Test: `tests/scripts/test_run_synthetic_case.py`
- Modify: `scripts/run_zero_buy_ablation.py`（`_NODE_MODULES` 追加 `"finance_agent.nodes.analysts"`；对应测试补一条 analyst patch 用例）

**Interfaces:**
- Consumes: Task 1 `build_synthetic_state`；`scripts.run_zero_buy_ablation.parse_variant_mapping/patch_prompts`
- Produces: `run_case(case: str, *, variants: list[Path] | None = None) -> dict`——返回 `{"case": str, "variants": dict, "decision": dict, "layers": dict}`；`decision` 为 final_trade_decision 的 dict 化；`layers` 为最终 state 全量可序列化 dump（K 线与三表替换为占位串）。
- Produces CLI：`uv run python scripts/run_synthetic_case.py --case perfect [--variant F ...] --out reports/synthetic/x.json`

**关键约束：**
- 变体映射扩展：文件名前缀 `fundamental_analyst_*`/`technical_analyst_*`/`macro_analyst_*`/`sentiment_analyst_*` → 对应 analyst prompt 名（在 runner 内实现 `_parse_mapping`，不依赖 `parse_variant_mapping` 的前缀规则扩展——后者保持 round-1 行为不变）。
- 运行器不写任何 DB、不派单；纯 `graph.invoke`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scripts/test_run_synthetic_case.py
"""合成案例运行器单测（mock 图，不真跑 LLM）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_synthetic_case import _parse_mapping, run_case


def test_parse_mapping_covers_seven_variants():
    m = _parse_mapping(
        [
            Path("d/trader_bprime.md"),
            Path("d/risk_judge_bprime.md"),
            Path("d/fund_manager_bprime.md"),
            Path("d/fundamental_analyst_bprime.md"),
            Path("d/technical_analyst_bprime.md"),
            Path("d/macro_analyst_bprime.md"),
            Path("d/sentiment_analyst_bprime.md"),
        ]
    )
    assert m == {
        "trader": "trader_bprime.md",
        "risk_judge": "risk_judge_bprime.md",
        "fund_manager": "fund_manager_bprime.md",
        "fundamental_analyst": "fundamental_analyst_bprime.md",
        "technical_analyst": "technical_analyst_bprime.md",
        "macro_analyst": "macro_analyst_bprime.md",
        "sentiment_analyst": "sentiment_analyst_bprime.md",
    }


def test_parse_mapping_rejects_unknown():
    with pytest.raises(ValueError):
        _parse_mapping([Path("d/unknown_bprime.md")])


def test_run_case_returns_decision_and_layers(monkeypatch):
    import scripts.run_synthetic_case as mod

    class FakeGraph:
        def invoke(self, state):
            return {
                "final_trade_decision": SimpleNamespace(
                    model_dump=lambda: {"action": "buy", "confidence": 0.72}
                ),
                "analyst_reports": {"fundamental": "ok"},
                "kline": "SHOULD_BE_OMITTED",
            }

    monkeypatch.setattr(mod, "build_variant_graph", lambda variant: FakeGraph())
    out = mod.run_case("perfect")
    assert out["case"] == "perfect"
    assert out["decision"]["action"] == "buy"
    assert out["layers"]["analyst_reports"] == {"fundamental": "ok"}
    assert "SHOULD_BE_OMITTED" not in str(out["layers"])
```

并在 `tests/scripts/test_run_zero_buy_ablation.py` 追加：

```python
def test_patch_covers_analyst_module(tmp_path):
    variant = tmp_path / "fundamental_analyst.md"
    variant.write_text("VARIANT-FUND", encoding="utf-8")
    import finance_agent.nodes.analysts as analysts_mod

    original = analysts_mod.load_prompt_with_meta
    with patch_prompts(tmp_path, {"fundamental_analyst": "fundamental_analyst.md"}):
        assert (
            analysts_mod.load_prompt_with_meta("fundamental_analyst").template
            == "VARIANT-FUND"
        )
    assert analysts_mod.load_prompt_with_meta is original
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/scripts/test_run_synthetic_case.py tests/scripts/test_run_zero_buy_ablation.py -v`
Expected: FAIL — 新文件 `ModuleNotFoundError`；analyst patch 用例 FAIL（模块未在 `_NODE_MODULES`）

- [ ] **Step 3: 实现**

```python
# scripts/run_synthetic_case.py
"""合成案例三连测运行器（2026-11-06 零买入第二轮 P0）。

纯离线：合成 fixture 直接进图（evals.ablation.build_variant_graph("full")），
不触网络、不派单、不写 sessions 库。变体注入复用 round-1 的 patch_prompts。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))  # evals/ 未安装进 venv（packages.find where=src）

from evals.ablation import build_variant_graph
from evals.synthetic_case import SYN_CODE, build_synthetic_state
from finance_agent.nodes.compute import compute_metrics
from scripts.run_zero_buy_ablation import patch_prompts

_ANALYSTS = ("fundamental_analyst", "technical_analyst", "macro_analyst", "sentiment_analyst")
_OMIT = (
    "kline",
    "benchmark_kline",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "quarterly_income",
    "peer_financials",
)


def _parse_mapping(paths: list[Path]) -> dict[str, str]:
    """文件名前缀 → prompt 名（含 4 分析师；与 round-1 的前缀规则正交）。"""
    mapping: dict[str, str] = {}
    for p in paths:
        stem = p.stem
        name: str | None = None
        for analyst in _ANALYSTS:
            if stem.startswith(analyst):
                name = analyst
                break
        if name is None:
            for fixed in ("trader", "risk_judge", "fund_manager"):
                if stem.startswith(fixed):
                    name = fixed
                    break
        if name is None:
            raise ValueError(f"无法从变体文件名推断 prompt 名: {p.name}")
        if name in mapping:
            raise ValueError(f"prompt {name} 重复指定: {p.name}")
        mapping[name] = p.name
    return mapping


def _dump_layers(final: dict[str, Any]) -> dict[str, Any]:
    """决策相关 state 全量 dump（大表/K 线替换为占位串，其余 model_dump/原样）。"""
    layers: dict[str, Any] = {}
    for key, value in final.items():
        if key in _OMIT:
            layers[key] = f"<{type(value).__name__} omitted>"
        elif hasattr(value, "model_dump"):
            layers[key] = value.model_dump()
        else:
            try:
                json.dumps(value)
                layers[key] = value
            except TypeError:
                layers[key] = str(value)[:8000]
    return layers


def run_case(case: str, *, variants: list[Path] | None = None) -> dict[str, Any]:
    """构造合成态 → compute_metrics → 全图 → 决策 + 分层 dump。"""
    state = build_synthetic_state(case)
    state.update(compute_metrics(state))  # type: ignore[arg-type]
    mapping = _parse_mapping(variants or [])
    variant_dir = variants[0].parent if variants else None
    if variants:
        dirs = {v.parent.resolve() for v in variants}
        if len(dirs) > 1:
            raise ValueError(f"多个变体须同目录: {sorted(str(d) for d in dirs)}")
        with patch_prompts(variant_dir, mapping):  # type: ignore[arg-type]
            final = _invoke(state)
    else:
        final = _invoke(state)
    raw = final.get("final_trade_decision")
    if raw is not None and hasattr(raw, "model_dump"):
        decision: dict = raw.model_dump()
    else:
        decision = dict(raw or {})
    return {
        "case": case,
        "variants": mapping,
        "decision": decision,
        "layers": _dump_layers(final),
    }


def _invoke(state: dict[str, Any]) -> dict[str, Any]:
    graph = build_variant_graph("full")
    return graph.invoke({**state, "focus": f"{SYN_CODE} 综合评估投资价值"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True, choices=("perfect", "defect"))
    ap.add_argument("--variant", action="append", default=[], help="变体文件（可重复）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    variants = [Path(v) for v in args.variant]
    result = run_case(args.case, variants=variants or None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    action = result["decision"].get("action")
    print(f"[synthetic] case={args.case} variants={result['variants']} action={action} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

修改 `scripts/run_zero_buy_ablation.py`：`_NODE_MODULES` 追加一行：

```python
    "finance_agent.nodes.analysts",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/scripts/test_run_synthetic_case.py tests/scripts/test_run_zero_buy_ablation.py -v`
Expected: 全部 PASS（含 4 条新用例）

- [ ] **Step 5: Lint + 提交**

```bash
uv run ruff format scripts/run_synthetic_case.py tests/scripts/test_run_synthetic_case.py scripts/run_zero_buy_ablation.py
uv run ruff check scripts/run_synthetic_case.py tests/scripts/test_run_synthetic_case.py scripts/run_zero_buy_ablation.py
uv run mypy scripts/run_synthetic_case.py
git add scripts/run_synthetic_case.py tests/scripts/test_run_synthetic_case.py scripts/run_zero_buy_ablation.py tests/scripts/test_run_zero_buy_ablation.py
git commit -m "feat(eval): 合成案例运行器——变体注入覆盖 7 prompt + 分层 dump"
```

---

### Task 4: 三配置运行 + 三岔口报告（非 TDD，验收明确）

**Files:**
- Create: `docs/evals/zero-buy-round2-p0-2026-11-06.md`

**跑法（逐条执行，产物落 `reports/synthetic/`，gitignored）：**

```bash
# 运行前记录生产库基线（应与 287/586 一致）
python -c "import sqlite3;c=sqlite3.connect('data/sessions.db');print(c.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])"

# run 1：生产提示词 + 完美案例（预期：不买——催化剂门槛必拦）
uv run python scripts/run_synthetic_case.py --case perfect --out reports/synthetic/p0-production-perfect.json

# run 2：B' 全拆 + 完美案例（三岔口关键）
uv run python scripts/run_synthetic_case.py --case perfect \
  --variant evals/variants/round2/trader_bprime.md \
  --variant evals/variants/round2/risk_judge_bprime.md \
  --variant evals/variants/round2/fund_manager_bprime.md \
  --variant evals/variants/round2/fundamental_analyst_bprime.md \
  --variant evals/variants/round2/technical_analyst_bprime.md \
  --variant evals/variants/round2/macro_analyst_bprime.md \
  --variant evals/variants/round2/sentiment_analyst_bprime.md \
  --out reports/synthetic/p0-bprime-perfect.json

# run 3：B' 全拆 + 缺陷案例（预期：不买——若买了，硬否决缺失）
uv run python scripts/run_synthetic_case.py --case defect \
  --variant evals/variants/round2/trader_bprime.md \
  --variant evals/variants/round2/risk_judge_bprime.md \
  --variant evals/variants/round2/fund_manager_bprime.md \
  --variant evals/variants/round2/fundamental_analyst_bprime.md \
  --variant evals/variants/round2/technical_analyst_bprime.md \
  --variant evals/variants/round2/macro_analyst_bprime.md \
  --variant evals/variants/round2/sentiment_analyst_bprime.md \
  --out reports/synthetic/p0-bprime-defect.json

# 运行后复核生产库基线不变
```

**变体注入核验**：三次运行都会产生 Langfuse trace；取每 run 的 trace，断言 generation 元数据中
`prompt_name` 为 `variant:trader` / `variant:risk_judge` / `variant:fund_manager` /
`variant:fundamental_analyst` 等（run 1 应为生产名+v 号）。核验脚本与 round-1 相同
（`/api/public/traces` 拉取 + observations.metadata）。

**报告必含（不许含糊）：**

1. 三配置决策原文（action / confidence / position_size / reasoning 全文要点）；
2. 三岔口裁决表（§背景判据三行，勾选落在哪一行）；
3. **若 B'+完美仍不买**：按 `layers` dump 逐层定位——trader 输出是什么动作？被哪一层改写（risk_judge / FM / 打回计数）？把「BUY 死于哪一层」写成一句可复核的话；
4. 若 B'+完美出 buy 且缺陷案例也不买：给出「锁在提示词」的结论，附 B' 清单与 P1/P2 入口；
5. 成本记录（三次运行的 Langfuse usage 合计）与生产库基线复核结果。

- [ ] **Step 1: 跑三配置**
- [ ] **Step 2: 注入核验 + 成本核算**
- [ ] **Step 3: 写报告并提交**

```bash
git add docs/evals/zero-buy-round2-p0-2026-11-06.md
git commit -m "docs(eval): 零买入 P0 三岔口报告——合成完美案例定位 BUY 死于哪层"
```

- [ ] **Step 4: 停下报告（停止点，不启动 P1）**

---

## P1–P4 门控大纲（P0 结果后单独立项，本计划不展开）

| 阶段 | 内容 | 启动条件 | 出口 |
|---|---|---|---|
| P1 | OpenSpec delta：四态 schema（BUY/WATCH/AVOID/INSUFFICIENT_DATA，数据缺失≠看空）、致命否决白名单、滞回仅约束翻转 | P0 裁决落「提示词层」或「拦截层」 | delta validate --strict 通过 + 契约测试 |
| P2 | 生产提示词改写（按 B' 清单 + 四态语言 + 置信度去锚定 0.50–0.60 示例值）+ `deploy_prompts.py` 发布 | P1 完成 | 契约测试绿 + 部署版本号 |
| P3 | 四用例回归门禁固化（必买/必观望/必回避/必报数据不足——复用 P0 fixture 加两个新 case） | P2 完成 | 每次改提示词必跑；验收=该买的能买、不该买的不买、理由可复核 |
| P4 | 真实票真空期验收（10 只事后超额 >+2% 的样本，对照 vs B'，撮合+结算全套） | P3 完成 | 报告落 docs/evals/ |

## 验收矩阵

| 阶段 | 验收证据 | 失败处理 |
|---|---|---|
| Task 1 | `uv run pytest tests/evals/test_synthetic_case.py -v` 6 passed | 修 fixture，不放宽断言 |
| Task 2 | 校验命令全部符合预期（锁字样清零、替换句就位、字段保留） | 检查基线是否用错版本（Step 1 diff） |
| Task 3 | `uv run pytest tests/scripts/test_run_synthetic_case.py tests/scripts/test_run_zero_buy_ablation.py -v` 全绿 | 修实现 |
| Task 4 | 三分 JSON 齐全 + 注入核验记录 + 报告落盘 + 判据三行之一被明确勾选 | 数据不全不得下结论；重跑缺的那次 |
| 全程 | 生产库 287/586 不变；P0 总 tokens ≤ 100 万 | 超支即停，报告现状 |

## 停止点（做到这里必须停下报告）

1. Task 4 报告完成、三岔口裁决落定 —— 停下向 owner 报告，P1 不得自行启动。
2. 若 run 2 出现「trader 输出 buy 但最终非 buy」——先别改任何东西，把该 run 的 layers 完整 dump 与 trace 链接交人审。
3. 任何需要判断「这条买入建议质量如何」的环节——交人工终裁。
4. 成本触顶（100 万 tokens）——停，不追加 run。

## 风险

| 风险 | 对策 |
|---|---|
| `build_variant_graph("full")` 与生产图守卫不一致（缺价位校验/打回环） | 若 P0 出「图内仍不买」而原因指向校验层，补一轮生产图确认（`build_5layer_graph` + patch `fetch_data` 返回合成态），作为 Task 4 的补充实验 |
| 合成数据被某个数据源二次拉取覆盖（peer/行业数据走网络） | fixture 的 `enable_web_search=False` + 键集含 `industry_pe/peer_financials=None`（真实路径同构的降级形态）；若出现网络取数，run 会显式失败而非静默 |
| 变体基线漂移（本地 ≠ Langfuse production） | Task 2 Step 1 的 diff 门；漂移时以 Langfuse 文本为底并在报告记录 |
| 分析师变体 patch 未生效 | Langfuse trace 元数据核验（variant:* 前缀）；不对即停 |
