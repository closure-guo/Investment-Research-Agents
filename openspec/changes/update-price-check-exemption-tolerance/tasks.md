# Tasks: update-price-check-exemption-tolerance

- [ ] T1 缺口 1（修正归因）：`_ENUM_SPAN_PATTERNS` 增 `(?<![\d.])\d{1,2}\.(?=\s)` 列表记号形态——301220 两轮「3. / 4. 」正例归零 + 「3. 若跌破 15.0」护栏负例仍阻断 + 小数价位 `20.67` 不受枚举豁免影响
- [ ] T2 缺口 2：`_NON_PRICE_SUFFIX` 增 ×——605033 round-1「2×日波动的 2」正例归零 + 「×后 15.0」护栏负例仍阻断
- [ ] T3 缺口 3：`_in_reference_bands` 带边吸附容差（|值−带边| ≤ 0.01，上下沿对称）——301220 round-1「20.67 vs 20.6714」正例归零 + 「20.60」护栏负例仍阻断
- [ ] T4 生产文本回归：301220 round-1/round-2 risk_judge reasoning 原文（trace 35b1efab/b34cdcf7）过修复后校验器归零
- [ ] T5 回归：决策价位校验全量 pytest + ruff check + ruff format --check + mypy（基线 98 不新增）
- [ ] T6 #592 回帖归因修正证据（Q3 → 编号列表记号）+ 生产部署验证 → 核关 #592
