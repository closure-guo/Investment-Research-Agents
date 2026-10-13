# OpenSpec 变更包：选股 + 因子挖掘 + 自动交易 + 记忆层

> 生成日期：2026-10-12。适用仓库：finance_analysis_agent。
> 将各 change 目录拷入仓库 `openspec/changes/` 后逐个实施；每个 change 完成后执行 `openspec archive <change-id>` 再开始下一个。

## 变更清单与依赖

| # | change id | 内容 | 依赖 |
|---|---|---|---|
| 1 | add-market-snapshot | 全市场日频快照（双复权口径/历史成分/状态快照/日批告警）——一切的地基 | 无 |
| 2 | add-matching-engine | 回测/仿真/实盘三位一体撮合引擎（T+1/涨跌停/分档滑点） | 1 |
| 3 | add-screening-filters | L1 确定性硬过滤 + 约束常量单一来源 | 1 |
| 4 | add-factor-dsl | 因子 DSL 解析器 + 复杂度硬约束 + AST 子树同构查重原语 | 无 |
| 5 | add-factor-proposal-agent | LLM 因子提案（合同化/双模型分工/跨桶采样） | 4 |
| 6 | add-factor-library-and-monitor | 因子库 + 五道闸准入（多重检验/池级组合增量）+ 分桶保种 + 衰减监控 | 1, 4, 5 |
| 7 | add-screening-ranker | L2 因子横截面打分 + Candidate 契约 + builtin 手工因子基线 | 3, 6 |
| 8 | add-analyst-factor-tools | 因子库只读工具注入技术分析师 + 消融变体 | 6 |
| 9 | add-screening-candidate-llm | L3 quick 模式 LLM 排雷 + 在榜稳定性门槛 | 7 |
| 10 | add-screening-pipeline | 选股日批编排 + 时效性校验 + 前端 /screening 页 | 9 |
| 11 | add-signal-portfolio-risk-gate | Signal 契约 + 组合仓位聚合 + 纯代码风控门禁 | 2, 3 |
| 12 | add-paper-broker | Broker 抽象 + 仿真撮合 + 虚拟/仿真战绩双曲线 | 11 |
| 13 | add-screening-evaluation | 候选池前向命中率 + 选股消融三变体 + 因子来源分层考核 | 10 |
| 14 | add-decision-memory | 情景记忆注入 + 语义记忆月批（人工确认）+ 记忆消融 | 无（建议靠后） |

## 建议实施顺序

```
阶段一（纯工程地基，无 LLM，先行）:
  1. add-market-snapshot
  2. add-matching-engine
  3. add-screening-filters
  4. add-factor-dsl

阶段二（因子管线）:
  5. add-factor-proposal-agent
  6. add-factor-library-and-monitor   ← builtin 因子在此随 ranker 准备

阶段三（选股漏斗跑通）:
  7. add-screening-ranker（builtin 因子先行跑通，LLM 因子后续入库加权）
  9. add-screening-candidate-llm
  10. add-screening-pipeline

阶段四（执行闭环）:
  11. add-signal-portfolio-risk-gate
  12. add-paper-broker（paper 至少跑两个月再评估实盘）

阶段五（评估与记忆，可与其他阶段并行启动设计）:
  8.  add-analyst-factor-tools
  13. add-screening-evaluation
  14. add-decision-memory
```

## 贯穿性纪律（各 change spec 已内嵌，此处汇总）

- 越上游越确定性：L1 过滤、五道闸、风控门禁、撮合引擎均为纯代码，LLM 无绕过路径。
- 一切新增能力带消融变体，配对 bootstrap 裁决；CI 含 0 只写「无显著差异」。
- 样本外切分口径与 evals/backtest 时点截断契约一致（change 6/13 引用同一纪律）。
- 每个 change 的验收红线：既有测试无修改通过；验证报告落 `tests/validation/`。
- 明确不做：RL 微调、市场生态制模拟、端到端 LLM 下单、本批次内的实盘接口（仅留 Broker 抽象）。
