# 回测设施试跑记录：pilot-2023-shock

**status**: active
**日期**：2026-09-01
**目的**：`evals/backtest/` 首跑，验证回放器在真实数据上端到端通畅（不追求投资结论）
**产物 JSON**：`reports/backtest/pilot-2023-shock-20260901-222355.json`（终跑）+ `…-210916.json`（首跑，K 线上下文缺失，见缺陷 #104）
**驱动脚本**：`tests/scripts/backtest_pilot_2023.py`

## 配置（硬上限内）

| 项 | 值 |
|---|---|
| regime | sideways（120 日窗口，沪深300 判定），决策日 2023-01-05 |
| 标的 | 002412 汉森制药 / 600519 贵州茅台 / 300308 中际旭创（3 只） |
| 重复 | 1 次/标的（共 3 次完整五层回放） |
| 管线模型 | deepseek-v4-flash（**偏差披露**：清单要求 deepseek-chat 官方，shell 的 DEEPSEEK_API_KEY 已失效（401）；先切 zen relay，zen 余额见底后经用户确认切火山方舟 agent plan。同为 deepseek 非 pro 档） |
| 数据源 | 财报=东财 datacenter（正常）；K 线=新浪（东财 push2his 本机不可达，进程内补丁 + 重试）；新闻/个股信息/实时行情拉取失败按非必需降级为空（fetch_data 既有语义） |
| 统计 | block bootstrap B=1,000（设施默认，高于清单下限 200） |

## 全链路验证结论

data_snapshot（前视截断）→ replay（TradeDecision）→ performance（CR/ARR/Sharpe/MDD）→ baselines（Buy-and-Hold/MACD/KDJ/RSI）→ significance（block bootstrap + 块长敏感性）**端到端通畅**（exit 0）。

### 人工核查三件事

1. **前视截断实证**（抽 002412 重建快照逐字段断言，`reports/backtest/truncation-check-002412.json`）：
   - K 线最新日期 = 2023-01-05（= 决策日，含决策日收盘价作入场价，符合既有语义）✅
   - 财报三张表最晚披露截止 2022-04-30（2021 年报法定截止）≤ 决策日 ✅
   - stock_quote / industry_pe / peer_financials 纯当下数据已剔除 ✅
   - **注意**：该核查依赖 harness 补丁把 K 线历史拉深到 1500 日；无补丁时 250 日默认值导致截断后 K 线为空（缺陷 #104，首跑即踩中）。
2. **结算语义**：复用 `outcome.settle.evaluate_decision`（T+1 起评、方向符号化 buy 为正其余为负），与 decision-outcome 一致；本次未触发涨跌停递延/停牌分支（watch 决策无入场）。⚠️ 方向符号化语义下 watch/hold 按 -1×持有期收益计分，属"避损正确性"口径而非持仓收益，绩效表解读需注意（设施已披露，沿用）。
3. **LLM 成本实测**（usage meter 全量真值，0 条估算）：

   | 指标 | 实测 | 全量外推（10 标的 × 3 regime × 3 重复 = 90 回放） |
   |---|---|---|
   | LLM 调用次数 | 80 次（≈27 次/回放） | ≈2,400 次 |
   | prompt tokens | 193,521 | ≈5.8M |
   | completion tokens | 303,822 | ≈9.1M |
   | 单回放均值 | ≈166k tokens | — |
   | 单回放耗时（ark plan） | ≈9 分钟 | ≈14 小时（串行） |

   completion > prompt 系 deepseek-v4-flash 思考链计入输出所致；费用以 ark 账单为准。

## 三标的绩效表（n=3，仅通路验证，无统计意义）

| 策略 | CR | ARR | Sharpe | MDD |
|---|---|---|---|---|
| 系统（3×watch，方向符号化计分） | -0.2314 | -0.6688 | -4.2816 | 0.2408 |
| Buy-and-Hold（最优基线） | 34.2257 | 0.2209 | 0.6666 | 0.7621 |
| MACD | 5.2414 | 0.1081 | 0.4764 | 0.5664 |
| KDJ | 11.6544 | 0.1528 | 0.5924 | 0.5590 |
| RSI | 5.9905 | 0.1151 | 0.6072 | 0.4762 |

- 方向一致率：3/3 标的 agreement=1.0（均 watch），无低一致率剔除。
- 超额 Sharpe 95% CI（vs Buy-and-Hold，截齐 60 个交易日）：**[-9.19, 2.95] 含 0 → 无显著差异**（spec 措辞约束）。
- 块长敏感性（10/20/40）CI 均跨 0，结论稳健。
- 基线 CR 为 1500 日全窗口累计收益（含 300308 约 10 倍涨幅），与系统单笔持有期收益** horizon 不可比**——绩效表内两者并列仅为设施输出格式，跨 horizon 数值不应直接对比（已在 methodology.ci_truncation 披露截断口径）。

## 试跑暴露的实现缺陷（逐条 issue，修复另行安排）

| Issue | 缺陷 | 状态 |
|---|---|---|
| #102 | `replay_decision` 把 pydantic `TradeDecision` 当 dict 用 `.get`，真实管线端到端必崩（设施从未真跑过的直接证据） | **已修**（TDD：复现测试先红后绿；修复属试跑阻断项，按 fix 流程执行） |
| #103 | 并发 fetch 下新浪 K 线回退间歇返回空，快照静默缺 K 线/基准，无失败信号 | 记录待修 |
| #104 | `build_snapshot` 继承 `fetch_data` 250 日 K 线默认值，历史决策日截断后 K 线确定性为空（首跑三标的 LLM 上下文因此无行情数据） | 记录待修；本次试跑以 harness 补丁（days=1500 + 重试）绕过 |

## 模型更正说明（2026-09-02 追加）

本文件回放使用 deepseek-v4-flash 作分析模型（deepseek-chat 官方 key 失效后的
错误替代）。后经用户裁决：试跑分析模型应为 glm-5.3（生产默认）。**本文件仅作
设施通路验证，不承载跨模型可比的投资/性能结论**——通路性结论（截断/结算/成本
记账/全链路通畅）与模型无关，依然成立；如需 glm-5.3 版回放数字需重跑（未执行，
pilot 定位不要求）。

## 环境偏差汇总（诚实披露）

1. 管线模型：deepseek-v4-flash（ark plan）替代 deepseek-chat 官方（key 失效）。
2. K 线/指数数据源：新浪替代东财（本机到 push2his.eastmoney.com 连接被重置）。
3. 新闻/关键事件/实时行情/个股信息：拉取失败按空降级（东财 news/spot/individual_info 接口同不可达）。
4. 宏观指标部分可得（4 个指标键）。

## 定位标注（2026-09-23 追加，Δ4 Task 4）

**通路验证 + 泄漏风险**：本文件为设施通路验证记录，**不是**可采信的 skill / 赚钱能力结论。

- **通路验证**：本文结论限于通路性（前视截断实证 / 结算语义 / 成本记账 / 全链路通畅），
  与所用分析模型无关；绩效表（n=3，仅通路验证，无统计意义）不得外推为能力主张。
- **泄漏风险**：决策日 2023-01-05 落在模型知识语料的时间范围内，存在知识泄漏风险，
  skill 结论不可采信；如需可采信读数，须走「干净窗口 + 泄漏探针」准入（delta
  add-backtest-leakage-controls）另行跑批。

本节为另起段落的追加标注：**上文原文与全部数字一律保留、未作改写**。

**过时表述指认（只指认，不改原文）**：上文「结算语义」一行称结算复用
`outcome.settle.evaluate_decision`，描述的是本记录撰写时的旧结算引擎；该模块已废弃
（delta `update-decision-settlement-contract`），回测结算已切到
`outcome.track_record.judgment.resolve_prediction`（horizon + 区间超额 + ±2% 中性带，
delta `add-backtest-leakage-controls`）。按「原文与数字保留」要求该行未改写，仅在此指认，
以免读者据过时描述推断当前结算语义。
