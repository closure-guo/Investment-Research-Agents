# 零买入第二轮 P0 · 合成完美案例三岔口报告（2026-11-06）

> 计划：[docs/superpowers/plans/2026-11-06-zero-buy-round2-synthetic-triage.md](../superpowers/plans/2026-11-06-zero-buy-round2-synthetic-triage.md)
> 执行：subagent-driven（Task 1–3 实现+评审全绿；fixture 经 v1→v3.1 三轮迭代与两轮评审修复）
> 产物：`reports/synthetic/v31-{production,bprime}-perfect.json`、`v31-bprime-defect.json`（本地）

## 1. 结论（三岔口裁决）

**落「B' 变体+完美案例仍不买」格 → 但逐层定位显示：不是提示词锁（已拆净）、也不是决策拦截层（五个决策闸门全 pass、FM approve、trader 自己就输出了 watch；citation 校验三次均未 pass，见 §2 末段披露，因自动重试关闭而未回写动作）——是行为层的两个判断：**

1. **时点/超买不追**：B' 臂 RM 评级已转「看多」、三方风险辩论「对方向偏多无分歧」，但 RSI 71.29 / J 值 95.83 / 距年内高点仅 1.16% → 裁决「超买只否决现在买，不否决以后买」；
2. **止损结构否决（第一轮同款机制的新形态）**：风控三方一致认定「17.48 止损距现价 -2.1%，仅约 1.1 个日波动，落在噪声带内」→ 「止损参考带不可作精确止损价」→ 落回 watch。B' 已把否决权限定成「降仓/放宽止损」，模型却用「该带不可用」绕回观望——**否决从「方向否决」进化为「执行参数否决」，结果相同：不买**。

**必回避验证通过**：缺陷案例（资不抵债+成交枯竭）在 B' 下输出 **sell（exit，0.62）**，理由引用负债 1100>资产 1000、归母权益 -100 亿、FCF 转负、健康度 64.2/8 项红灯——风控行为完整，拆锁没有拆掉避险能力。

**数据缺口语义**：B' 臂模型自发写下「宏观/新闻舆情/解禁等属双向缺口，不能反向读作看空，只能压低置信度」——用户框架第 1 条（数据缺失≠看空）已在模型判断层面出现；但系统输出层仍无 INSUFFICIENT_DATA 态，缺失只能落成 confidence 降档（0.5 中档）→ 制度化的四态仍是 P1 的事。

## 2. 三配置结果（v3.1 同 fixture）

| 配置 | RM 评级 | trader 自出动作 | 最终动作 | 置信度 | 关键裁决 |
|---|---|---|---|---|---|
| 生产提示词 + 完美 | 中性 | watch | **watch** | 0.50 | 催化剂门槛（20 日内兑现路径缺失）→ 首轮同款 |
| **B' 全拆 + 完美** | **看多** | **watch** | **watch** | 0.50 | 方向偏多成立；超买→不追；止损带宽 1.1σ→不可用；数据缺口→压置信 |
| B' + 缺陷 | 看空 | sell | **sell** | 0.62 | 资不抵债/现金流断裂/健康度红灯 → exit |

**逐层 dump 证据（run 2）**：`final_price_check / final_inaction_check / final_trigger_check / decision_price_gate / holdings_executability_check` 五个决策闸门全部 `pass`；`fund_manager_decision = approve`；`trader_plan.action = watch`。**BUY 没有死在任何决策拦截节点——它从未被产生。**（run 1 的 `final_trigger_check` 带注 `打回后已申报`——触发位校验确实回退重跑过一次，run 1 最终动作仍为 watch。）

**citation 闸门如实披露（三次 v3.1 运行均未 pass）**：`citation_pass=False`、`citation_blocked=True`、`citation_coverage_gap=True`，覆盖率 0.80–0.85（三次分别为 0.82 / 0.80 / 0.85，生产完美 / B' 完美 / B' 缺陷）；B' 完美的失败桶为 `path_unresolvable=4 + semantic_term_mismatch=2`。`CITATION_AUTO_RETRY_ENABLED = False`（`src/finance_agent/routing.py:42`）→ 非 PASS 只登记告警、不回写 action，因此 citation 闸门不改变本轮裁决与动作；但引文覆盖缺口仍在 15–20%，不能读作「citation 全绿」。

## 3. 与计划判据表的差异说明

计划判据第三行为「B'+完美仍不买 → 锁在图内拦截层或行为层，转入 P1 的 schema+拦截修复」。实际落点比该行更精确：**拦截层已用层 dump 排除**（trader 本身即 watch；run 1 仅触发位校验回退重跑一次、未改写动作，见 §2 注）。行为层有两个成分，需分开处置：

- **可归为纪律合理的**：超买不追、止损须宽于噪声——这两条与「满足条件时必须敢买」不矛盾（因为这轮的条件在模型看来并未满足：时机与执行结构都不理想）。**但需记录一个结构性偏差**：系统只接受「回踩支撑型」入场，对「强势突破/动量型」入场存在系统性回避——真空期上涨的股票常是动量型，这正是零买入的结构成因之一。
- **应归为待修的**：止损结构否决的回路——trader 提议窄止损（因价格贴近年内高点、下方最近结构位在 -1.5%）→ 风控以「窄于噪声」否决 → 无任何一方被要求执行「放宽止损至 ≥2σ 再建仓」或「降仓位档位接受更宽止损」的正向修复 → 默认落 watch。B' 的「否决只能降仓/放宽止损」条款存在，但**没有触发正向重写路径**（模型选择了放弃而非协商），这是行为层的真实缺陷。

## 4. Fixture 迭代记录（P0 的一次教训）

首版 fixture（v1）不满足「低估值」前提：无 market_cap/PE/PB → 估值分位缺失 → RM 只给「中性」；K 线平滑线性 → 波动率 5.96%/回撤 1.46%/VaR 0.107%/beta 恰 1.0（基准与个股同序列）→ 风险辩论以「刻度不自洽」否决仓位。两轮运行后判定 fixture 无效，迭代为：

- **v2**：三表 ×1e8（元口径）、quote 补 market_cap 3780 亿/PE 12/PB 5.0、industry_pe=18（GARP pass）、K 线真实波动（σ≈1.9%/日、年化 30%、回撤 17.9%）+ 尾部（回调+修复+量增）、基准独立序列；
- **v3**：个股收益由基准驱动（beta 0.89，消除独立随机流伪影）；
- **v3.1**（评审修复）：三表年期对齐 3/3/3、留存收益勾稽（validator 0 warnings）、PB 与市值/权益一致、元单位/基准独立性/beta 回归断言加固；defect 案例 PB 置 `None`（资不抵债、归母权益为负时 PB 无意义；market_cap/PE 保持原值并由测试锁定）。

**产物命名**：v1 两 run（无效 fixture）的产物已重命名为 `reports/synthetic/v1-invalid-production-perfect.json` 与 `reports/synthetic/v1-invalid-bprime-perfect.json`。

**已知残差（记入下一轮）**：v3.1 的尾部修复斜率（+0.6%/日，为满足「末收盘<年内高点」的最低可行值）仍造成 RSI 71 / J 95.8 的过热读数，「超买异议」有 fixture 成分。下一轮 fixture 可改为「回踩至 MA20 上方 3-5%、RSI 55-65 收尾」形态，消除该成分后重跑 B' 臂——预期结果二分：仍 watch → 行为层结论坐实（止损结构缺陷为主因）；出 buy → 超买残差是唯一主因，修复方向即提示词时点纪律校准。

**严格二分版补测（2026-11-06 17:00–17:09，已完成）**：采纳「只降 RSI、不动支撑距离」的更严设计，v4 冷却尾 fixture（`perfect_cooled`）由测试锁死（`a7c5135`）——12 日回调 + 6 日修复 + 7 日横盘微跌，末收距 250 日高点 -2.27%、RSI14=61.95 / J=58.18（v3.1 为 71.29 / 95.83）。B' 补跑结果为 **`buy`（0.5，light，17.66/16.80/19.00）**：超买论点消失、风控接受 4.87% 止损、五决策闸门全 pass。按预注册判据落「超买残差是主卡点」。**但严格版未锁住止损距离**（交易员把止损锚在平台下沿/MA60 带，-4.87%≈2.5σ，而非近端低点 -1.9%）——因此更准确的结论是：两个假设在上游合并为同一条因果链「高位入场 → 窄止损 → 风控否决 → 默认 watch」；P1 优先级应把「入场时点/价格结构纪律」提到第一，止损协商回路降为第二。（**修订 2026-11-07**：该排序被 H2 直接测试修正——收盘序列与 v4 逐字一致、仅 ATR 腰斩的 v5 仍 watch，止损几何是独立阻塞器，两闸门并列推进，见 [H2 报告](zero-buy-round2-h2-stopband-2026-11-07.md)。）详见 [严格二分补测报告](zero-buy-round2-bisection-2026-11-06.md)。前三次尝试被供应商过载打断（bull_r1 / 中途 / fundamental_analyst，`RateLimitError`），无产物；宿主侧主模型 `openai/deepseek-v4.1-flash` 期间返回 AuthError（governor），流量落在 fallback `openai/kimi-for-coding`；按成本纪律停止重试后于 17:00 窗口一次通过。

## 5. 成本与完整性

| 项 | 数值 |
|---|---|
| P0 运行消耗（Langfuse usage 实测，v1 两 run 145k + v3.1 三 run 292k；v1 run1 部分 trace 滑出查询窗，保守估算全量 ≈ 58 万） | **≈ 43.7 万～58 万 tokens**（闸门 100 万，闭合） |
| 生产库完整性 | 跑前跑后 `data/sessions.db`：predictions 287 / sessions 586 逐字不变 |
| 副作用 | 无派单、无 sessions/predictions 写入、无服务重启 |
| 变体注入核验 | run 2 的 trace generation 元数据 `variant:trader/risk_judge/fund_manager/4 分析师` 全部命中（Task 4 核验记录）；run 1 为生产名+v 号 |
| 基线核验（评审复核） | 本地 `src/finance_agent/prompts/{trader,risk_judge,fund_manager,4 分析师}.md` 与 Langfuse production 文本逐字比对：**7/7 一致（仅尾部换行差异）**——Task 2 Step 1 门禁闭合，生产基线无漂移污染 |
| 产物完整性 | v3.1 三 run：`v31-{production,bprime}-perfect.json` / `v31-bprime-defect.json`；v1 两 run 为无效 fixture，产物已重命名 `v1-invalid-*`。**口径注**：三份 v3.1 产物由修复前的 dumper 生成，`analyst_reports / debate_history / risk_debate_history` 三层在 8000 字处被截断；本报告引用的 action / RM 评级 / 五闸门等均为独立顶层 key，未受截断影响；后续运行改用无损 dump（见 `scripts/run_synthetic_case.py::_json_safe`） |

## 6. 后继动作（P1 立项前待 owner 拍板）

1. **P1 的四态 schema 维持立项**：数据缺口语义已经由模型自发「不反向读作看空」，但制度上没有 INSUFFICIENT_DATA 态（只能压 confidence）——这与「缺失数据必须显式化」的验收标准冲突，仍然是该做的那块契约。
2. **新增一条 P1 范围**：**止损结构协商回路**——当风控否决「止损窄于噪声」时，流程必须产生一次「放宽止损/降仓位」的重写机会（而非默认 watch）。这是本轮定位到的行为层缺陷，属于决策流程契约，不是提示词措辞。
3. **fixture 残差修复后再跑一轮 B'**（§4 残差项）：单次 ≈ 10 万 tokens，用于把「超买残差」从行为层结论中剥离。**已完成（2026-11-06 17:00–17:09）：B' × perfect_cooled = buy（0.5，light）；超买论点消失、风控接受 4.87% 止损、五闸门全 pass。止损距离未被严格版锁住（自选 −4.87% vs 近端低点 −1.9%），故归因修正为「高位入场→窄止损→否决」同一条因果链。见 [严格二分补测报告](zero-buy-round2-bisection-2026-11-06.md)。**
4. **本轮只支持一条提示词结论：拆掉 7 条周期契约锁不足以产出 buy**。提示词层的残余偏置尚未隔离——`risk_judge_bprime.md:39` 的「证据均衡时倾向 hold/watch」、trader/risk_judge 中 0.4–0.7（示例 0.55）的置信度锚、以及分析师 `horizon_hint` 单条编辑就使 RM 从「中性」转为「看多」（生产 vs B' 完美产物），均属 P1 待决事项，本轮不下结论。
