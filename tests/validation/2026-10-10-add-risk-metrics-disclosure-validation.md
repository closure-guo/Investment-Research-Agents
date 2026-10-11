# add-risk-metrics-disclosure 人工验证报告（issue #242 断点 1）

- 日期：2026-10-10
- Delta：`add-risk-metrics-disclosure`（openspec/changes/add-risk-metrics-disclosure/）
- 范围：报告口径披露节新增「风险指标」「价位参考」两行确定性渲染
- 验证性质：确定性渲染层变更——**单元测试即终验**，无需实跑 LLM 分析（渲染输入为 state 结构化键，不经任何 LLM）

## 1. 光大 601818 场景复核（断点 1 关闭判据）

原缺口（guangda-bank-report-audit 裁定 P0-3）：风控数字（年化波动率 15.68% / 最大回撤 18.68% / VaR 1.73% / beta 0.004）与触发价位（3.21 / 2.92 / 3.05 / 3.55 / 3.86）首现于决策与风控节，报告内无出处。

以光大审计原值构造 state 直测 `_format_freshness_section`（tests/nodes/test_report_freshness_render.py::TestRiskMetricsAndPriceLevelsDisclosure）：

| 原缺口数字 | 披露节渲染结果 | 断言 |
|---|---|---|
| 波动率 0.1568 | `年化波动率 15.68%` | ✓ |
| 最大回撤 0.1868 | `最大回撤 18.68%` | ✓ |
| VaR95 0.0173 | `VaR95 1.73%` | ✓ |
| beta 0.004 | `beta 0.004` | ✓ |
| 入场参考 3.21 | `入场参考 3.21` | ✓ |
| 止损带 2.92–3.05 | `止损带 2.92–3.05` | ✓ |
| 目标带 3.55–3.86 | `目标带 3.55–3.86` | ✓ |

同源判据：渲染输入即 `state["risk_metrics"]`（calc_risk 输出，state.py:91）与 `state["price_levels"]`（calc_price_levels 输出，state.py:95）——与决策/风控辩论消费的是同一份结构化数据，不存在第二口径。

## 2. 边界与零回归

| 场景 | 期望 | 结果 |
|---|---|---|
| beta 键缺席（基准不可得） | 省略 beta 段，不渲染「暂缺」 | ✓（beta 缺席是数据语义非字段缺失） |
| price_levels.available=False | 诚实标注「价位参考不可用（reason）」 | ✓（reason 原文带出，如 insufficient_kline） |
| 两键全缺 + 旧数据源全缺 | 节整体返回 None，不出现 | ✓（与现状逐字节一致） |
| 仅有旧数据源（health_score） | 新增行不出现 | ✓ |
| 节内标题纪律 | 无 `##`/`###`（不破坏导出切章） | ✓ |

## 3. 自动化验证汇总

- `pytest tests/nodes/test_report_freshness_render.py`：9 passed（新增 4 用例先红后绿）
- `pytest tests/nodes/test_report.py`：34 passed
- `pytest -m "not live"` 全量：见本目录对应会话记录（0 失败）
- `ruff check` / `ruff format --check` / `mypy`（report.py）：零错误
- `openspec validate add-risk-metrics-disclosure --strict`：valid

## 4. 范围外（不在本 delta）

- 断点 2：锚点校验升级（被锚定论述须在上游报告中可检索、含数字）
- 断点 3：FM 审批面 += 估值完整性（valuation_snapshot.missing_reasons）+ 披露-叙述一致性
