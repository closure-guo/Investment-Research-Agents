# Tasks: refactor-price-check-dimension-classifier

- [ ] metrics/price_text.py：NumberDimension 归类 + extract_commitments 账本 + 别名/绑定随迁，直测用例先红后绿
- [ ] decision_price_check.py 收编为消费方（10 张表移出、_check_snippet 改走账本）
- [ ] 既有 91 个行为测试原样全过（行为不变证明）
- [ ] spec MODIFIED 段落归类化（三段 token 豁免 → 量纲归类契约），validate --strict 通过
- [ ] 全量回归 + ruff/mypy/format 三连
