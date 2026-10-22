# Tasks: fix-price-gate-meta-discourse

## 1. 实施

- [x] 1.1 `_in_meta_discussion` 句子级元话语判定（分句符 + 15 个标记）
- [x] 1.2 主循环与方向预扫同时跳过自引用句
- [x] 1.3 测试：打回消息复述 / MA60 纠正说明 / 被驳回方案 / 正常行情陈述护栏

## 2. 同批修复（非本 delta 范围，记录关联）

- [x] 2.1 健康度评分 / σ 记号 / 年份+报告期三类漏排除（commit `e3637537`）
- [ ] 2.2 32 条历史阻断离线重放（哨兵：误报桶清零、E 桶 11.3/21.5 结论落表）

## 3. 验证与收口

- [ ] 3.1 `uv run pytest tests/metrics tests/nodes/test_risk.py tests/nodes/test_validate_trade_prices.py` 全绿（本机 2026-10-22 已过：351 passed）
- [ ] 3.2 真实链路：一轮 cohort（≥10 标的）确认阻断数下降且无新增幻觉放行
- [ ] 3.3 更新 `docs/incidents/034-decision-price-check-false-positives-20261002.md` 处置记录
- [ ] 3.4 `openspec validate fix-price-gate-meta-discourse --strict` + sync
