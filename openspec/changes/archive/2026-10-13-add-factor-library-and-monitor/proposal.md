# Proposal: add-factor-library-and-monitor

## Why

- LLM 批量提案在统计上必然产出假显著因子（挖 1000 个总有几十个"看起来有效"），无多重检验校正的因子库会被噪声塞满。
- 单因子 IC 准入忽略因子是组合使用的：各自 IC 高但彼此冗余的因子对组合无增量——入库标准必须是池级组合增量而非单因子指标。
- 因子会衰减（拥挤/市场结构变化），无滚动监控的因子库会持续给出过期信号。

## What Changes

- 新增 `factors/library.py` + `factors/monitor.py` + `factors/validate.py`：
  - 因子库存储（SQLite）：表达式 AST、hypothesis、category、分桶、IC 时序、状态（candidate/active/degraded/retired）、入库/降级时间。
  - 表达式求值引擎：DSL → 快照数据上的向量化计算（横截面，消费 `market-snapshot`）。
  - 五道闸准入（按单位成本升序执行，任一不过即拒并记原因）：①可计算性（缺失率 ≤20%）→ ②单因子 RankIC/ICIR（|ICIR| ≥ 0.3）→ ③多重检验校正（同批次内做置换检验，p 值经 BH-FDR 控制，q=0.1）→ ④池级组合增量（候选因子加入当前 active 池后，池等权组合的样本外 IR 增量 > 0；另做与池内因子的 |IC 相关性| ≤ 0.7 冗余检查）→ ⑤样本外复核（训练/验证按时间切分，切分口径与 evals/backtest 时点截断契约一致）。
  - 双层查重：AST 子树同构（结构层，复用 factor-dsl 原语）+ 数值层 IC 相关性。
  - 分桶保种：按 category 分桶，每桶独立保留 top-k（k=10）active 因子。
  - 衰减监控日批：每日重算 active 因子近 60 日滚动 IC；滚动 IC 均值连续 20 个交易日为负且 |滚动 ICIR| < 0.1 时自动降级为 `degraded` 并从打分权重中移除；降级事件落库可审计。

非目标（Out of scope）：

- 不做实盘权重优化（组合优化属执行层 delta）。
- 不引入 Qlib 等外部表达式引擎（自研向量化求值，后续性能不足时再评估）。
- 不做因子语义自动命名等体验优化。

## Capabilities

### New Capabilities
- `factor-library`: 因子的准入闸、池级增量评估、分桶保种与衰减监控。

## Impact

- **后端**：`src/finance_agent/factors/library.py`、`factors/validate.py`、`factors/monitor.py`、`factors/eval_engine.py`（新增）。
- **测试**：每道闸正反单测；置换检验种子可复现单测；池级增量计算单测；衰减降级状态机单测。
- **验证**：用 5 个已知有效经典因子（动量/反转/换手/波动/市值）与 5 个随机噪声因子跑准入管线，前者应入库、后者应被拦，报告落 `tests/validation/`。
