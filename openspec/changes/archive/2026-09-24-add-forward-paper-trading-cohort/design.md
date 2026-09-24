# Design: add-forward-paper-trading-cohort

## Context

outcome 链已建成：predictions 落库（append-only + 冻结 + 防篡改哈希）→ 每日 16:00 结算 → 16:30 盯市 → 16:40 integrity_check → Score 回写 → 只读 API。缺的是样本：用户流量可执行率 11%、标的与时点不可控、settled <10。Δ1 协议把 forward 腿定为金标准，Δ2 把窗口缩到 T+20——cohort 是把这两者变成可读数的样本泵。约束：后端单 uvicorn worker（StreamRegistry 进程内），跑批不能与用户 SSE 流量互相踩踏；LLM 成本真实发生（≈166k tokens/次 deep）。

## Goals / Non-Goals

**Goals:**

- 定向、可预算、可审计地积累 forward 观点样本（含 neutral 的回避判定样本）
- 观点走真实管线与真实结算链路，cohort 侧零特判、零口径分叉
- 跑批与用户流量隔离（时移 + 串行 + 开关 + 预算上限）

**Non-Goals:**

- 不做模拟撮合、组合再平衡、资金曲线（观点级结算已够 Δ1 主指标；组合级属后续增量）
- 不改 predictions schema（加列方案被否，见 D1）
- 不裁决 Trader watch 姿态（#134）
- 不自动产出结论（收口走 Δ1 纪律，人工终裁 + 两句式）

## Decisions

**D1 记账走独立 `cohort_runs` 表，不给 predictions 加 cohort 标记列。**
备选：predictions 加 `cohort_tag` 列。否决理由：① predictions 是 append-only + 快照冻结模型，Δ2 刚为 avoidance_status 动过一次 schema，再叠加跨 delta 的同需求 MODIFIED 会触发并行变更 rebase 规则（project-workflow §6），协调成本大于收益；② cohort 记账本来就需要独立表（每轮 run 的成本/状态/ universe_version 不属于观点粒度）；③ join 键现成（session_id / langfuse_trace_id）。评估侧导出 = `cohort_runs JOIN predictions`，战绩页对外语义不变（cohort 观点是真实 live 观点，本就应计入）。

**D2 标的池 = 沪深300 分层抽样，登记文件版本化。**
与 `decision-backtest`「分层市场状态抽样」的分层精神一致（行业/市值），抽样脚本带种子可复现。池内标的固定：换池必须新版本号，保证「同一 cohort 版本内读数纵向可比」。默认 N=10 是成本与样本速度的折中——10 标的/日 × 11% 可执行率 ≈ 1 条可执行观点/日，T+20 窗口下 6–8 周可积累 ≥10 settled 可执行样本（Δ1 红线）；owner 可按预算调 N 与频率。

**D3 调度 = APScheduler 进程内 job（复用 outcome/scheduler.py 先例）+ 时移窗口 + 串行。**
备选：外部 cron 调 API。否决：外部触发同样进单 worker 事件循环，隔离性无差异，反而丢失 misfire/coalesce 既有硬化（#57.4 语境）与进程内记账便利。盘后窗口（默认 18:00 起，可配置）串行跑：单次 ≈9 分钟 × 10 标的 ≈ 90 分钟，远离用户高峰；跑批期间用户请求正常抢占事件循环（async 管线本就协作式）。收盘后决策 → entry_price 自动落「次一交易日收盘」口径（Δ2 D2），无特殊处理。

**D4 成本护栏双层：单轮 token 预算上限（超限停止后续标的 + 告警）+ 全局开关（默认关）。**
成本随轮落 cohort_runs（LLM 调用数 / token 数，从 run 的 usage 回流取真值——#152 预算校准同源机制）。开关默认关防止部署即烧钱；开启为显式运维动作。

**D5 幂等键 = (universe_version, ticker, 交易日)。**
同日重复触发跳过（除非显式 force，force 行记账 run_seq 递增）。防 scheduler 重启重放、防手工误触双跑——与结算 job 幂等同款纪律。

**D6 失败隔离：单标失败跳过 + 记账 failure 状态与原因，不重试不阻塞。**
数据源大面积不可用日（r5 先例：东财当日大面积失败）整批可能高比例 failure——记账如实反映，健康检查（Δ1 收口）消费 failure 率；不在跑批层做重试（结算层已有行情缺失重试语义）。

## Risks / Trade-offs

- [cohort 观点混入战绩页对外展示，用户看到系统自产跑批观点] → 观点本就是真实 live 产出，不隐藏；如 owner 认为需要区分展示，后续增量在 API/前端加 cohort 过滤维度（记账表已备好 join 键）
- [盘后跑批与 16:00 结算 job 的时间耦合：当日新观点 T+0 即 open] → 结算语义按 horizon 交易日计数，观点创建当日不参与自身结算，无竞态；跑批窗口默认 18:00 在结算/盯市/integrity（16:00/16:30/16:40）之后
- [单一 universe 的标的选择偏差] → 登记文件记录抽样方法与种子；Δ1 收口报告披露 universe 版本与分层构成；换池走新版本纵向分段
- [LLM 预算失控] → D4 双层护栏 + 每轮成本落账 + Δ1 预登记成本分型申报
- [跑批期间后端重启] → APScheduler MemoryJobStore 重启后 job 重注册（既有先例），当日未完成批次由幂等键支持次日补跑或手工 force

## Migration Plan

纯增量：新表（幂等迁移）、新模块、新开关（默认关）。上线即静默；开启 cohort 为运维显式动作，首轮建议 owner 在场观察成本与耗时。回滚 = 关开关，无数据清理需求（cohort_runs 与观点行都是 append-only 事实）。

## Open Questions

- 池规模 N 与频率的预算终裁（owner；默认 10 标的/交易日，≈1.7M tokens/日）
- 跑批窗口默认 18:00 是否与运维作息冲突（可配置，非阻塞）
