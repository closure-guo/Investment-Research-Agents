# 因子库类型覆盖度缺口盘点（2026-11-27）

**问题**：我们的因子库还缺什么类型的因子？

**一句话结论**：库现在是一个「单票日频量价库」——DSL 只有 6 个价量字段，在册 12 条 active 全部落在成交量 / 振幅 / K 线形态三个子族；标准 A 股因子分类里其余十几族不是「做了没做好」，而是结构上进不来。缺口分四种性质（A 数据面没有 / B DSL 没开口 / C 有供给但全死在闸口 / D 评估口径不利），对应四种修法。

标记：`[实测]` 本次亲手读出（只读 API + 容器内 JSON 文件，未碰 SQLite）；`[文档]` 文档或记忆记载（带日期）；`[推断]` 未经验证。

---

## 1. 库实态

一句话：库里 12 条在册因子全是「今天成交量和振幅怎么样」这一类信号，其余类型为零。

来源：`GET /api/factors`、`GET /api/factor-research/status`、`GET /api/factor-research/harvest`（2026-11-27 18:26 前后），容器内 `data/factor_research/{explored_factors,harvest_state,candidates}.json`。

- `[实测]` 在册 12 条 active + 2 条 retired。
- `[实测]` 12 条 active 的构成（按表达式人工归类）：
  - 成交量族 8 条：量比（#1）、量变异系数（#2）、量自相关（#8）、量比 20/60（#9）、量价相关 ×3（#10、#267、及退役同构 #7 见下）、量集中度（#12）、log 成交量（#274）
  - 振幅族 3 条：#3、#4、#11
  - K 线形态 1 条：#6
  - 动量/反转 0、收益波动率 0、横截面/相对强弱 0、非量价 0
- `[实测]` 60 日 RankIC 均值全负（−0.012 ~ −0.090），|ICIR60| 0.20 ~ 0.42。池级增量（pool_increment）只有 #1（+0.358）、#2（+0.180）可观，其余 10 条 ≤ 0.10（其中 8 条 ≤ 0.04）。
- `[实测]` 分类标签失真：10 条 active 的 `category` 仍是兜底值「外部」（另 2 条为「价量」「流动性」）。回喂文件 `explored_factors.json`（生成于 2026-10-30）里风格缺口块读到的是 `pool_by_category = {"外部": 10}`——反馈回路看不出池子是「8 量 + 3 振幅 + 1 形态」。`[文档]` `update-harvest-factor-category` 任务 4/5，「生产迁移执行 + 次日收割实跑验证」未勾。
- `[实测]` 同构异桶：#7 `ts_corr(close,volume,10)` 于 2026-10-22 因 `BUCKET_QUOTA` 退役；#267 `ts_corr(close, volume, 10)`（仅空格差异）于 2026-11-01 经提案路径、以 `category="价量"` 重新入库。
- `[实测]` 最近一次入库是 2026-11-01（提案月批 2 条）。2026-11-27 16:31 收割作业运行 54ms，无新增。

## 2. 供给侧

一句话：外部生成器已经停了近四周；它停之前提的 2444 条候选几乎全是价量想法，其中七成多连闸都没摸到。

- `[实测]` RD-Agent 生成器 `desired_running=false`（请求时间 2026-10-29 23:00:34）；`candidates.json` 生成于 10-29 22:53；`harvest_state.json` 止于 10-30 16:11。`[文档]` 这是 2026-11-09 A2 终裁后的既定状态（研究开关维持关闭，`docs/evals/2026-10-31-因子供给判效读数.md` §9.1）。
- `[实测]` 累计漏斗：2444 候选 → 不可译 1772（72.5%）+ 转写错误 19 → 进闸 653 → 入库 12（进闸者的 1.8%）。进闸后拒收 641：闸② 490、闸④ 88、闸⑤ 22、闸⓿ 26、闸① 15、闸③ 0。
- `[实测]` 不可译 1772 条归因（关键词分类，首中即停；与 10-27 审计口径同量级）：

  | 原因 | 条数 | 占比 | 阶段 |
  |---|---|---|---|
  | 复杂度超限（自由参数 >3 / 深度 >8） | 814 | 45.9% | translator |
  | 横截面 / 矩阵 / 回归 / 市场 / ML | 757 | 42.7% | llm |
  | 不支持的算子（滚动偏度、exp、if/where、安全除法） | 98 | 5.5% | llm |
  | epsilon 未给值 | 82 | 4.6% | llm |
  | 负号 / 语法 | 20 | 1.1% | translator |
  | 缺数据列 | 1 | 0.1% | — |

- `[实测]` 候选宇宙 99% 以上是价量。描述里真正指向非价量数据的候选：资金流/筹码 0、分析师 0、事件 0、宏观 0；基本面/估值的正则命中均为误命中（如 Trend Quality、yields）。唯一一条事件型想法 `LIMIT_UP_FREQ_20D`（近 20 日接近涨停的天数占比）用 9.5% 收益率做代理，死在复杂度超限。
- `[实测]` 被锁死的需求（描述提及数 → 去向，三集合有重叠）：横截面运算 1033 条（42%）→ 974 不可译；回归/残差/PCA/贝塔/ML 325 条（13.3%）→ 315 不可译；市场/大盘收益 112 条（4.6%）→ 112 条全部不可译。
- `[实测]` 内部提案月批的 category 枚举只有（价量 / 反转 / 动量 / 流动性 / 波动率）——`src/finance_agent/factors/propose.py:49-50`，spec 逐字。
- `[实测]` builtin 5 条基线无一在册；拒绝池 builtin 桶 4 条（闸② 3 / 闸④ 1）。
- `[实测]` 拒绝池（唯一结构 231，`explored_factors.json` 2026-10-30）：外部-其他 72 / 量与流动性 56 / 动量反转 45（闸② 41）/ 波动率 39（闸② 30、闸④ 7）/ 量价交互 15 / builtin 4。

## 3. 表达能力边界

一句话：DSL 能说的只有「用一只股票自己的开高低收量和成交额做时序统计，再做一次截面排名」。

- `[实测]` 字段 6 个：`open/high/low/close/volume/amount`（`src/finance_agent/factors/dsl.py:72`）。
- `[实测]` 算子 21 个（`dsl.py:43-70`）：时序 `ts_mean/ts_std/ts_corr/ts_rank/ts_sum/ts_max/ts_min/ref/delta`；截面 `rank/zscore`；逐元素 `add/sub/mul/div/max/min/abs/log/sqrt/sign`。
- `[实测]` 复杂度上限：深度 8 / 自由参数 3 / 字段 3（`dsl.py:120-122`）。
- `[实测]` 表达式在单票自身序列上求值，再做截面 `rank`/`zscore`；`src/finance_agent/factors/` 与 `src/finance_agent/screening/` 下无任何中性化代码（grep `neutral|中性化` 零命中）。表达式看不到别的股票、看不到日期。
- `[实测]` 快照表 18 列（`src/finance_agent/data/snapshot.py:54-74`），DSL 只暴露 6 列。闲置列：`pct_chg`、`prev_close`、`close_raw`、`is_st`、`is_suspended`、`limit_up_sealed`、`limit_down_sealed`、`listed_days`、`list_date`。
- `[实测]` 风格篮子 5 个且全为代理（`style_exposure.py:50-52` → `builtin.py:64-105`）；无股本数据，换手率以量比代理、市值以成交额代理（`builtin.py:15-25`）。
- `[实测]` 评估口径：T+1 RankIC、|ICIR| ≥ 0.3、分层单调性 ≥ 0.6、准入面板 500 交易日（`constraints.py:55,57,77,83`）。多 horizon 待裁决（`openspec/changes/optimize-factor-supply-funnel/tasks.md:28`，C2）。
- `[实测]` 系统里已有但没接到因子链的数据面：研报库 `research_reports.db` 的 `earnings_forecasts`（逐日一致预期快照）与 `rating_changes`（评级变动、目标价）——`src/finance_agent/data/research_reports_store.py:60-97`。覆盖度未核。

## 4. 缺口清单（按性质）

| 因子族 | 现状 | 性质 | 补齐前置 |
|---|---|---|---|
| 事件/状态：涨停/大跌天数、次新股 | **修正（写 spec 时核对发现）**：快照的状态位列（涨停封板 / ST / 停牌）在历史回填区是写死的 0（`scripts/backfill_market_snapshot.py`），只有最近约 30 个交易日是真值，不能作因子输入；上市天数可由主表上市日期派生，涨跌停天数可由价格用比较算子派生 | B | 比较算子 + `listed_days` 派生字段 |
| 规模（真实市值/流通市值）、真实换手率 | 只有成交额/量比代理 | A | 快照补总股本、流通股本（慢变列）+ DSL 字段 |
| 行业相对、行业中性化、行业动量 | 无行业字段，全库无中性化 | A+B | 行业分类列 + neutralize/group 算子，或评估侧中性化 |
| 市场/贝塔、残差动量·残差反转、特质波动 | 无指数序列、无回归算子 | B | DSL 加 `ts_regression` + 市场收益序列 |
| 横截面组合（rank/zscore 再加工、相对强弱） | DSL 有 rank/zscore，中间语言无 CS 算子 | B（中间表示） | B1 已立项待评审 |
| 高阶时序：偏度/峰度、EMA/线性衰减、滚动中位数/分位、距高低点天数、条件分支 | 无算子，约 118 条候选因此不可译 | B | DSL 算子扩展 |
| 动量/反转、收益波动率（价量内部） | 被拒 45 / 39 个结构，0 在册 | C（闸②） | 准入问题，非类型问题（见 10-28 杠杆分析） |
| 估值/价值、盈利质量、成长、杠杆 | 0 候选 0 入库；分析层能逐票取数，无 PIT 面板 | A（+D） | 带公告日的时点财务面板 + DSL 字段 + 多 horizon 评估 |
| 事件驱动：业绩超预期、业绩预告、解禁、指数调样、分红回购 | 0 | A | 事件面板（带公告日） |
| 分析师预期：一致预期修正、评级变动、覆盖度 | 0；研报库已有表 | A（数据部分已有，未接线） | 落地 `add-analyst-revision-factors` |
| 资金流/筹码：两融、北向、股东户数、龙虎榜、机构持仓 | 0 | A | 新数据源 |
| 情绪/文本/另类：新闻、搜索、调研 | 0（生成器只把「情绪」当解释用语） | A | 需 LLM 抽取或外部数据 |
| 宏观/择时/市场宽度/日历效应 | 0（表达式看不到别的股票和日期） | B | 面板级算子 / 日期字段 |
| 日内/逐笔微观结构 | 0 | A | 日内数据 |
| 多时间尺度（T+5、T+20、月频） | 全库只评 T+1 | D | C2 裁决 |

## 5. 已识别 / 在途（不重复立项）

- `[文档]` `openspec/changes/add-analyst-revision-factors/`（0/10）：把研报库接进同一套五道闸，首批 `eps_rev_1m` / `rating_chg_1m` / `coverage_chg_1m`，以预计算值矩阵（非 DSL 表达式）进闸。设计已写，未实施。
- `[文档]` `openspec/changes/add-cross-section-translation-path/`（0/9）：B1 中间表示改造，解锁横截面构念（案 A Qlib 映射桥 / 案 B 直出 DSL）。待评审。
- `[文档]` `optimize-factor-supply-funnel`：B3 批次去同族化、C1 epsilon 默认值、C2 多 horizon 裁决均未做；D1 面板 500→750 日约 2027 年初自然解锁；D2 观察项。
- `[文档]` 10-28 杠杆分析明确不建议：降 |ICIR| 门槛、放宽转译纪律、用验证段调参。
- `[文档]` 10-27 供给链审计：不可译里 87% 是设计边界，真正可扩的时序算子约 7%——本次实测同量级（98 + 20 条）。

## 6. 优先级建议（推断）

1. **最便宜（已修正）**：原文建议「把快照现成列开口给 DSL」——写 spec 时核对发现状态位列在回填区是写死的 0，不能直接开口。可行的最便宜项改为三件：①比较与条件算子（从价格派生涨跌停天数等事件计数）；②自由参数改按去重数值计数（实测存量复杂度桶 814 条里约 398 条因此可过，上限不动，与治理裁决 D4「不增加拟合自由度」同向）；③把已在手的市值 / 换手率接进来（新浪历史日线带流通股本与换手率、腾讯批量给当日值、拥挤度日批每天 15:35 已在全市场取）。行业中性化做成显式算子 `ind_neutralize`，不改全局评估口径，存量 12 条读数不变。
2. **换一个供给来源**：落地 `add-analyst-revision-factors`。它是第一个非量价族，与现役池天然低相关（闸④最容易过），数据面已建成。代价是覆盖宽度受研报库限制。
3. **恢复供给吞吐**：B1（横截面桥）+ 回归残差算子 + 市场收益序列。437 条候选（325 + 112）几乎全部翻不过来，是供给侧最大的单一损耗源。但它改善的是「供给效率」，不是「类型覆盖」——解锁出来的仍是价量变体。

最大的一块空白是基本面（估值/质量/成长），但 `[推断]` 慢变量在 T+1 RankIC、|ICIR| ≥ 0.3 的口径下天然吃亏，即使接进来也可能被闸②成批拒收。建议排在 C2（多 horizon）裁决之后；动工前先用样本做一次 T+1 vs T+20 的离线 IC 对比，成本很低。

**三处要一起改才有效果**：DSL/数据面能表达（B/A）+ 提案 prompt 的 category 枚举会去提（`propose.py:49-50` 现在只有价量五类）+ 有人在提（外部生成器现为关闭状态；内部月批按 spec 只提价量）。只改其中一处，库里不会多出任何新类型。

## 7. 未取到 / 未验证

- SQLite 未读（宿主不碰活跃 WAL 库的红线）：拒绝池逐条表达式与闸口读数、2026-11-27 复苏通道首波是否提交、`factor_diagnostics` 的风格暴露读数。
- 研报库（`earnings_forecasts` / `rating_changes`）对全市场的覆盖度未核。
- 不可译归因与候选族分类是关键词规则，非互斥处已注明；「慢变量在 T+1 口径吃亏」未做实测。
- 后台调研 agent 因供应商 5 小时限额失败，本笔记由主会话完成。

## 8. 后续（同日）

- 已立 delta `openspec/changes/extend-factor-library-coverage/`（proposal / design / specs ×5 / tasks；`openspec validate --strict` 通过；待 owner 评审，未改代码）。
- 数据源补测（2026-11-27，容器内只读探测）：两融明细（上交所 `stock_margin_detail_sse`、深交所 `stock_margin_detail_szse`，2025-03-12 的历史可取）、龙虎榜（新浪 `stock_lhb_detail_daily_sina`，历史可取）、新浪财务指标（逐票 86 列，按报告期）均可用；新浪历史日线带 `outstanding_share` 与 `turnover`；腾讯批量行情字段 38 / 44 / 45 给换手率 / 流通市值 / 总市值。行业批量源在本机 AKShare 1.18.63 下函数名与文档不符（只找到 `stock_board_industry_name_ths`、`sw_index_third_cons`），巨潮逐票 `stock_industry_change_cninfo` 可用（带变更日期）。

## 9. 已落地（2026-11-28 收口）

delta `extend-factor-library-coverage` 的 P1/P2 已实施并部署（P1 `ee8faeba` / P2 `32c8d034`）：

- **P1（DSL 表达力）**：自由参数改去重计数（上限 3 不变；离线确定性复译 405/836 解锁）+ 19 个新算子（OPS 21→40）+ 翻译器 15 映射与一元负号改写；治理 D5 落 `docs/factor-governance.md`。
- **P2（参考数据面）**：`market_ref_daily` 侧表 + `industry_l1` 列 + 读路径 JOIN/旧库退化 + 面板可选字段与 `industry` 分组；拥挤度日批顺手落库；历史回填 2,942,506 行、覆盖率 99.79–99.94%；闸①字段/分组预检；同构重提桶归属（前置 category 迁移 12 条已执行）；builtin 五条→七条（真实口径两条 `size_float` / `turnover_20` 闸①通过、闸② ICIR −0.0664 / −0.2151 拒收）；L2 `factor_value_gaps` 披露（生产快照重放为空列表）；提案 category 九类 + prompt 已发布。
- **未闭环**：行业门类探针 36/50 = 72% < 90% 门禁 → 全量回填按 spec 不执行，`industry_l1` 仍 0 覆盖（`ind_neutralize` 类因子继续闸①拒收）；需换行业源或提升 cninfo 命中率后重跑探针。
- 逐项读数与挂账：`tests/validation/2026-11-28-extend-factor-library-coverage-p2-validation.md`。

## 来源

- 代码：`src/finance_agent/factors/{dsl,builtin,constraints,style_exposure,propose,harvest}.py`；`src/finance_agent/data/{snapshot,research_reports_store}.py`；`src/finance_agent/api.py:3983-3999, 4729-4735`
- 规范与变更：`openspec/changes/{add-analyst-revision-factors,add-cross-section-translation-path,optimize-factor-supply-funnel,update-harvest-factor-category}/`
- 文档：`docs/evals/2026-10-31-因子供给判效读数.md`；`docs/superpowers/plans/2026-10-28-optimize-factor-supply-funnel-b2-style-gap.md`；`docs/factor-governance.md`
- 实态：`GET /api/factors`、`GET /api/factor-research/status`、`GET /api/factor-research/harvest`（2026-11-27）；容器内 `data/factor_research/explored_factors.json`（2026-10-30）、`harvest_state.json`（2026-10-30）、`candidates.json`（2026-10-29）
- 记忆：`factor-admission-levers-20261028`、`factor-supply-audit-20261027`、`analysis-audit-20261124`
