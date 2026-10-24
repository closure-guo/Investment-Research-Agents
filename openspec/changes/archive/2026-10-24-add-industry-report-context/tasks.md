# Tasks: add-industry-report-context

- [x] 行业名口径实测：5 个标的实测完成（电池/乘用车命中，白酒/多元化保险未命中）；词表快照与结论落 `tests/fixtures/industry_report_vocab.tsv` + `tests/validation/2026-10-22-industry-report-industry-vocab-match.md`
- [x] `normalize_industry` + 层级后缀剥离 + 单元测试（NFKC、全角空格、`Ⅱ/Ⅲ/II/III`）
- [x] `INDUSTRY_ALIASES` 显式别名表 + 测试（别名值必须存在于词表快照；覆盖 多元化保险→保险）
- [x] `list_industry_reports` 只读查询 + 单元测试（时点、窗口、条数、降序、空态）
- [x] `get_industry_digest` 聚合 + 单元测试（ok / stale / unmatched / 空库 None / 异常 fail-open / 未来剔除 / 别名命中）
- [x] `format_industry_digest` 渲染 + 单元测试（「仅元数据」披露、防锚定、stale 说明、unmatched 不得说「无研报」）
- [x] `fetch_data` 注入 `industry_report_digest` + 单元测试（行业缺失 → None、异常 fail-open；抽取为 `_attach_report_digests` 后可测）
- [x] `state.py` 新增 state 键
- [x] 基本面上下文注入 + 单元测试（有数据注入、无数据不占位、其他分析师不消费）
- [x] citation 回声源纳入 digest 标题 + 单元测试
- [x] `prompts/fundamental_analyst.md` 数据清单增行，并 `py scripts/deploy_prompts.py` 发布（`fundamental_analyst` 4283 chars；导入 1 / 跳过 20 / 失败 0）
- [x] 回归：定向 `tests/data + tests/nodes + citation` **1107 passed**；全量 `pytest` **6674 passed / 10 failed**（已逐条归因，均非本 delta：8 项为 Windows GBK 子进程解码、在 clean HEAD worktree 复现；1 项 `test_fm_decision_live` 需真实 LLM；1 项 `test_report_trigger::test_per_report_cap` 依赖联网 freshness）；fetch 抽取重构后 `tests/nodes + test_graph_5layer` **704 passed**；`ruff` 变更文件全过；`mypy` 2 条报错均落在未改动行（`research_reports.py:770`、`fetch.py` 既有 `fallbacks` 字典），属基线**
- [x] `openspec validate add-industry-report-context --strict` 通过
