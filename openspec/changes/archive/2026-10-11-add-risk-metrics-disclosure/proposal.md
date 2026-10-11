# Proposal: add-risk-metrics-disclosure

## Why

issue #242 断点 1（光大银行 601818 报告评估 P0-3，2026-10-05）：波动率 15.68%/最大回撤 18.68%/VaR95 1.73%/beta 0.004/触发价位 3.21/2.92 首现于交易决策与风控辩论节，四个分析师节零命中，报告的确定性口径披露节（`_format_freshness_section`）不含它们——读者无法溯源。数字本身是 `metrics/risk.py` / `metrics/levels.py` 的确定性计算（已精确复现），非幻觉；缺的是 grounding 链的最后一米。

## What Changes

报告口径披露节（`_format_freshness_section`，report-decision-rendering 既有确定性渲染块）增列两行确定性披露：

- **风险指标行**：`state.risk_metrics`（年化波动率/最大回撤/VaR95(历史模拟法)/beta）——百分数 ×100 两位小数渲染，beta 仅在基准可得时存在（缺失省略该段而非渲染暂缺）；逐项缺失按既有 `_item` 模式渲染「暂缺」。
- **价位参考行**：`state.price_levels`——`available=True` 时渲染入场参考价/止损带/目标带（low–high 区间）；`available=False` 时渲染「不可用（reason）」诚实标注（对齐估值缺失的诚实文案语义）。

两键全缺时不增行（零回归）；节块结构/标题不变（行内追加，不产生新章节——导出切章与段数断言不受影响）。断点 2（锚点校验升级）与断点 3（FM 审批面）不在本 change 范围，另行立项。

## Capabilities

- `report-decision-rendering`：新增 Requirement「风险指标与价位参考口径披露」（ADDED）。

## Impact

- `src/finance_agent/nodes/report.py`（`_format_freshness_section` 增两行渲染分支）
- `tests/nodes/test_report.py`（新增披露行测试类）
- 风险：stub 管线产出 80 行 K 线 → risk_metrics/price_levels 均会计算，新行出现在 stub 报告——已核验渲染为节内列表项（不改章节数），段数敏感断言不受影响；前端恢复路径渲染原样 markdown 无需改动。
