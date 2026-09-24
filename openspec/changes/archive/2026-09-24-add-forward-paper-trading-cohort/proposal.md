# Proposal: add-forward-paper-trading-cohort

## Why

证明赚钱能力的金标准是 forward 读数（真实管线、真实时点、零泄漏），但生产用户流量积累不了样本：可执行决策仅 11%（取证报告 §2），已结算样本 <10，且流量标的/时点不可控。日历时间不可压缩——cohort 晚启动一天，首个读数就晚一天。需要一个定时跑批机制：固定标的池按频率走真实分析管线，观点自然落 predictions、走既有结算/盯市链路，定向积累可结算样本。这不是新结算系统，只是给已建成的 outcome 链加装「样本泵」。

## What Changes

- 新增 **paper-trading cohort 跑批机制**：
  - **标的池登记与版本化**：从沪深300 成分按行业/市值分层抽样固定 N 只（默认 10，可配置），登记文件含成分清单、分层依据、抽样种子、版本号、生效日期；版本生效期内 SHALL NOT 变更（换池 = 新版本，记账带 universe_version）
  - **定时跑批**：按配置频率（默认每交易日一次，盘后时移窗口）对池内标的串行执行 deep 分析，走与 `/api/analyze` 相同的真实代码路径（观点自然落 predictions，source_type=live）；同标的同日幂等；单标失败（数据源不可用等）跳过记账、不阻塞整批
  - **跑批记账**：新表 `cohort_runs`（run_id / universe_version / ticker / session_id / trace_id / 触发时间 / 状态 / LLM 调用数与 token 成本），评估侧经 trace_id/session_id join predictions 导出 cohort 观点清单与结算状态——**predictions 表零 schema 变更**
  - **成本与运维开关**：显式开关默认关闭；每轮成本汇总落记账，超单轮预算上限停止后续标的并告警；串行执行 + 时移窗口，不与用户 SSE 流量争抢（单 uvicorn worker / StreamRegistry 约束）
- cohort 观点结算**零特判**：与用户流量观点走完全相同的判定链路（T+20 窗口、回避判定、Score 上报），满足 Δ1「双腿互证」的 forward 腿口径

## Capabilities

### New Capabilities

- `paper-trading-cohort`: 定时跑批定向积累可结算观点样本——标的池登记与版本化、定时跑批与记账、成本预算与运维开关、cohort 读数导出

### Modified Capabilities

（无——predictions 表与结算链路零改动；记账走独立新表）

## Impact

- 代码：新增 cohort 跑批模块（调度接线复用 `outcome/scheduler.py` APScheduler 先例）+ `cohort_runs` 表（`data/sessions.db` 同库，便于 join）+ 标的池登记文件与抽样脚本（`scripts/`）+ 评估侧导出脚本（`tests/scripts/` 或 `evals/`）
- 运维：env 开关（默认关）+ 预算上限配置；成本实测参照——单次 deep 全流程 ≈166k tokens / ≈9 分钟（pilot 实测），默认 10 标的/日 ≈1.7M tokens/日，**预算为 owner 决策点**（池规模与频率都可下调）
- 数据：cohort 观点进 live 战绩（真实管线产出，战绩页可见，无隐藏流量）；评估读数经 join 过滤，不依赖前端改动
- 依赖：实现顺序在 `update-decision-settlement-contract`（T+20 窗口与回避判定）之后；开跑前置 = Δ1 预登记生效（无预登记跑批只产出「样本积累中」数据，不出结论）
- 边界：不裁决 #134（Trader watch 姿态）——cohort 样本中 neutral 观点走回避判定，同样产生读数；不引入模拟撮合/组合再平衡（paper trading 只做观点级结算，组合级净值属后续增量）
