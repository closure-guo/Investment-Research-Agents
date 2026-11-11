## ADDED Requirements

### Requirement: 小票 lane 日报行

日报 SHALL 新增小票 lane 独立行，内容至少含：当日 microcap 池规模、小票批分析数
（新建会话数）、跳过的跨池重叠数、派发失败数、小票账本当日净值（可得时）。主档
「自动选股分析」行的会话统计 SHALL 仅计入 `pool='main'` 运行行，MUST NOT 因
`pool` 列引入而混入小票行。数据缺失时 SHALL 写「无数据」，MUST NOT 推测补齐。

#### Scenario: 主档行不混入小票行

- **GIVEN** 当日主档批 10 行、小票批 10 行均已落 pool_analysis_runs
- **WHEN** 日报生成
- **THEN** 主档自动选股行统计仅含 main 行（10），小票行统计仅含 microcap 行（10）

#### Scenario: 小票行字段披露

- **GIVEN** 当日小票批 selected=10、跳过 4、dispatch_failed=1，小票账本净值可得
- **WHEN** 日报生成
- **THEN** 小票行如实披露上述数字与净值
- **AND** 净值不可得时写「无数据」
