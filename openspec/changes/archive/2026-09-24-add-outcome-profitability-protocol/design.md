# Design: add-outcome-profitability-protocol

## Context

系统已有两条通往「收益读数」的建成通路，但都没有可信读数：

- **生产战绩链**（`src/finance_agent/outcome/`）：predictions 落库（append-only + 快照冻结 + 防篡改哈希）→ 每交易日 16:00 结算 → 16:30 盯市 → Langfuse Score 回写 → 总览/校准/分段 API。已结算样本 <10，且 09-07~09-22 有 66 行测试泄漏污染（incident 031，已清理，conftest 全局隔离护栏已落）。
- **离线回测链**（`evals/backtest/`）：as-of 快照（K 线按日截断、财报按法定披露日截断）→ 完整 5 层图重放 ×3 → 结算 → block bootstrap CI + 4 条基线。仅跑过 pilot-2023-shock（n=3，全 watch，通路验证）。

评估纪律的机器（预登记门禁字段、两句式结论、报告生命周期 status 头、校准门控）已在因果消融 v2 上验证过，本 delta 把同一套纪律接到 outcome 侧。

## Goals / Non-Goals

**Goals:**

- 在第一个收益读数产生之前锁死口径（窗口 / 基准 / 胜率定义 / 人口划分 / 红线），防止事后口径漂移
- 给两条读数腿（forward cohort、walk-forward 回测）一个共同的预登记与收口流程
- 把「观测数据会撒谎」「指标低 ≠ 能力差」两条项目教训固化为 outcome 侧的收口前置检查

**Non-Goals:**

- 不裁决「agent 是否赚钱」——本 delta 只立规矩，不出读数
- 不做层间归因（B6 维持 suspended：「哪层赚钱」是「整体是否赚钱」证实之后的问题）
- 不改结算行为、不改决策契约（在 `update-decision-settlement-contract`）
- 不动 Trader watch 姿态（#134 owner 产品裁决，与评估解耦）

## Decisions

**D1 主窗口 = T+20 交易日。**
理由：与旧 decision_log 的 `MAX_HOLD_DAYS=20` 语义连续；A 股月度尺度与决策申报的 entry/stop/target 交易语义匹配；样本积累速度可比 T+252 快一个数量级（252 默认下第一批生产读数要等一年）。备选：T+252（现行默认，等待不可接受）、T+5（噪声主导，事件研究尺度不匹配决策语义）。T+5/T+10 保留为盯市派生的辅助观测窗口（daily_marks 已每日落库，零新增采集）。窗口值同时写入 Δ2 的配置项 `OUTCOME_DEFAULT_HORIZON_DAYS=20`，口径与实现一处定义、两处引用。

**D2 胜率沿用既有 ±2% 中性带 resolved 口径。**
track-record「判定规则」「基础统计」已定义 resolved_win/loss/neutral 与胜率分母规则，outcome 协议直接引用，不发明第二套胜率——否则前后端战绩页与评估报告会给出两个「胜率」。

**D3 watch/hold 以「回避正确率」作辅助指标，不进主结论。**
生产流量 89% 为中性决策（取证报告 §2），不计分则评估人口只剩 11%。回避语义：horizon 到点按 long 口径超额，< −2% → 回避正确（该跌没参与），> +2% → 回避错误（错过上行），带内 → 回避中性。定位为辅助的原因：① 防止「watch 恒对」的解释滑向为不行动辩护（Goodhart 风险）；② 主结论（赚钱能力）在语义上只属于承担方向的决策。实现落 Δ2（avoidance_status 独立字段，不混入 resolved_* 与 win_rate）。

**D4 泄漏控制框架：forward 为金标准，回测腿带降级句式。**
as-of 快照能截断数据，截不断 LLM 参数化记忆——决策日落在模型训练语料内时，回测「skill」可能是「记忆」。框架：forward cohort 读数无泄漏假设；回测腿须过「干净窗口」判定 + 泄漏探针（实现与阈值在 Δ4），探针超阈则该批降级为「泄漏污染下的上界证据」，不得单独作为赚钱能力结论；深历史批次（如 2023）永久定位通路验证。两腿分歧时，forward 腿优先采信，分歧本身必须归因后才可解释。

**D5 结论两句式沿用 causal_ablation 纪律。**
合法结论仅两句式：**显著为正/为负**（带效应量与 CI）｜**分辨率不足**（带 MDE 与扩样方向）。裸「未获统计支持」非法。低胜率读数必须先分桶归因（数据缺口 / 结算缺陷 / watch 语义误读 / 模型真错误）+ 人工终裁，才允许进入处置（改 prompt / 判定 agent 缺陷 / 写进报告结论）——incident 026 教训在 outcome 侧同样成立，且 outcome 读数对外可信度诉求更高，纪律只能更严。

**D6 样本量与 MDE。**
首个 forward 读数的最低门槛 = settled 可执行样本 ≥10（红线），完整结论建议 ≥30（对齐 track-record「样本量 ≥30 完整展示」既有门槛）。MDE 在预登记文档中按功效反算锁定（复用 `mde_paired_binary` / 簇 bootstrap 的既有换算模式，推断单元 = 标的）。回测腿样本量由 MDE 反算 + 预算上限共同决定（Δ4 预登记批次落实）。

**D7 收口产物位置。**
收敛报告落 `docs/evals/`（校准与复盘报告先例）+ 机器可读全量产物落 `reports/outcome/`（对齐 `reports/evals/*.json` 惯例，本地不入库）；归因/终裁对照表落 `tests/validation/`。metrics.md 仍是唯一口径查询入口，不建副本。

## Risks / Trade-offs

- [T+20 窗口下超额收益受市场 regime 支配] → 超额口径（vs 000300）已对冲 beta；报告强制附 regime 分段披露（track-record-segments 已有市场环境切片维度）
- [回避正确率被误读为「watch 也赚钱」] → 辅助指标定位写进口径定义与报告模板；主结论段落 SHALL NOT 引用回避指标
- [可执行样本积累慢（11% × 流量）导致 forward 腿长期 <10] → 正是 Δ3 cohort 的立项理由（合成流量定向积累）；协议不为此放宽红线
- [预登记锁死后发现口径错误] → 走 metrics.md §1 修订 + 新预登记版本 + 时间线切点，不静默改（与 judge rubric 版本化同款纪律）

## Migration Plan

纯增量：metrics.md 加节、预登记文档新增、evals 侧工具新增。无生产行为变化，无需回滚策略。Δ2 落地前，本协议的读数条款处于「已登记、未启用」状态（时间线切点行注明）。

## Open Questions

- 主窗口 T+20 的 owner 终裁（备选 T+10/T+60）——影响 Δ2 配置默认值与预登记文档，不影响本协议结构
- 泄漏探针降级阈值（Δ4 建议 60% 记忆命中率）——owner 终裁后写入预登记
- forward cohort 的标的池规模与跑批频率预算（Δ3 owner 决策点）
