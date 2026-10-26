# Tasks: feed-dsl-contract-to-factor-generator

- [x] 导出载荷含 `contract` 块（复杂度上限 import 单一真相源 / 算子与字段白名单 / 三条表达规则）与 `gate_rejections` 聚合（TDD，tests/factors/test_feedback.py）
- [x] distro 消费端渲染契约块（先契约后清单、共享字符预算、无契约 fail-safe 回落旧行为）（TDD，新测试文件加载 spike 源）
- [x] `scripts/export_factor_feedback.py` 摘要行披露契约在位
- [x] 部署：消费端覆盖 distro site-packages + 宿主手动导出刷新 `explored_factors.json` + distro 内实跑渲染验证（含契约文本可见）
- [ ] 后端容器重建（避开红线时窗 + sessions/jobs 前置检查）使日批自动导出带契约块（**合并后待办**）
- [x] 人工验证报告落 tests/validation/（含渲染产物实跑证据）
