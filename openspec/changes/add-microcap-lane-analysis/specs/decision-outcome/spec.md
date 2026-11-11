## ADDED Requirements

### Requirement: 决策 lane 归属

`predictions` SHALL 增加 `lane TEXT NOT NULL DEFAULT 'main'` 归属列（幂等 ALTER，
存量行自动 main，无需回填）：

- ingest 落库时 SHALL 按 `session_id` 反查 `pool_analysis_runs.pool` 确定归属：命中
  则取该池值（小票批次会话 → `microcap`），查不到（人工会话 / 主档批次会话 / cohort
  等非池会话）SHALL 落 `main`；
- 反查失败 SHALL NOT 阻断落库（回退 `main` 并记 WARN）；
- 预测结算（日主判定 / horizon 结算 / Score 反向上报）SHALL lane 无关——所有观点
  照常按既有规则结算；lane 仅作账本归属与统计切分维度；
- 同日日主判定（duplicate_of_day）语义保持 lane 无感，MUST NOT 因引入 lane 而改变
  同 (symbol, 归属日) 去重行为。

#### Scenario: 小票会话观点落 microcap lane

- **GIVEN** 某 (trade_date, symbol) 在 pool_analysis_runs 有 `pool='microcap'` 行，
  其 session_id 产生观点
- **WHEN** 观点 ingest 落库
- **THEN** 该行 `lane='microcap'`

#### Scenario: 非池会话观点落 main lane

- **GIVEN** 观点的 session_id 在 pool_analysis_runs 无行（人工 / cohort 会话）
- **WHEN** ingest 落库
- **THEN** `lane='main'`

#### Scenario: 反查失败降级不阻断

- **GIVEN** ingest 时 pool_analysis_runs 查询异常
- **WHEN** 观点落库
- **THEN** lane 回退 `main` 并记 WARN
- **AND** 观点照常落库，业务管线不中断

#### Scenario: 结算与去重 lane 无感

- **GIVEN** 同日同 symbol 存在 main 与 microcap 两条 open 观点（人工触发等场景）
- **WHEN** 日主判定执行
- **THEN** 同 (symbol, 归属日) 去重规则与变更前一致（仅 created_at 最晚者进结算，
  其余 `duplicate_of_day`）
- **AND** 两池观点的 horizon 结算与 Score 上报行为不变
