# Tasks: add-risk-metrics-disclosure

## 1. 披露行渲染（TDD 先红）

- [x] 1.1 失败测试：risk_metrics 全字段（含 beta）→ 披露节含「年化波动率/最大回撤/VaR95/beta」行，百分数两位小数（0.1568 → 15.68%）
- [x] 1.2 失败测试：risk_metrics 无 beta 键（基准不可得形态）→ beta 段省略，其余三项照常
- [x] 1.3 失败测试：price_levels available=True → 披露节含入场参考/止损带/目标带行（区间值精确到 4 位小数的原文值）
- [x] 1.4 失败测试：price_levels available=False（reason=insufficient_kline）→ 「价位参考不可用」诚实标注行
- [x] 1.5 失败测试：两键全缺 → 披露节不增行（与现状逐字节一致，零回归）；三旧数据源也全缺时节整体仍返回 None
- [x] 1.6 实现 `_format_freshness_section` 两分支渲染

## 2. 验证与回归

- [x] 2.1 tests/nodes/test_report.py 全绿（既有披露节断言不受新增行影响——行内追加非新章节）
- [x] 2.2 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [x] 2.3 openspec validate --strict 通过
- [x] 2.4 光大场景复核：披露节含 15.68%/18.68%/1.73%/0.004 与触发价位带（tests/validation 人工验证报告，stub 数据确定性渲染截图或文本摘录）
