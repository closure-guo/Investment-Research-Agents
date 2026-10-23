# 决策保守度归因（离线，2026-10-22）

> 结论先行：r15 已排除「confidence 段写法」这个变量后，**「执行参数缺失逼出 watch」这一假设在现有数据里站不住**——参数缺失是 watch 的结果（契约使然），不是独立致因。下一轮单变量应改测「措辞规则」，而不是「补参数」。

## 1. 读数（sessions.final_trade_decision，2026-10-15～10-22）

| 项 | 值 |
|---|---|
| 有终稿决策的完成会话 | 52 |
| watch | 37（71%） |
| sell | 15（29%） |
| buy | 0 |
| 37 条 watch 中带结构化触发位（`trigger_high`/`trigger_low`） | **37/37** |
| watch 理由含「参数」/「position_size」 | 13 |
| 含「缺位」 | 5 |
| 含「写死」 | 3 |
| 含「缺失」 | 19 |
| 含「口径」 | 29 |

数据源：`data/sessions.db` 只读查询；`final_trade_decision` 为 risk_judge 终稿 JSON。

## 2. 归因

1. **自指结构**：`position_size/entry_price/stop_loss/target_price` 在 `action=watch` 时按契约就是 null（无仓位即无参数）。决策层随后把「参数未定义」写进 `inaction_reason` 作为不行动理由，等于用结果解释原因。
2. **参数并非不可得**：37/37 的 watch 都申报了 `trigger_high/trigger_low`，说明 `price_levels`（止损带/近期高低点）在 state 里是可用且被消费的；「拿不到价位所以只能观望」与数据不符。
3. **方向偏空**：watch 71% + sell 29% + buy 0，与「全 watch」印象不同——执行性决策并非为零，只是全部在做空侧；这与 10 月行情/技术面一致，暂不判定为缺陷。

结论：置信度写法不是主因（r15 阴性），参数缺失是**同义反复**。真正需要检验的是「理由表述规则是否在鼓励用自指字段做挡箭牌」。

## 3. 下一轮单变量（预登记草案）

- **变量（唯一）**：watch/hold 的 `inaction_reason` 表述规则——MUST NOT 以「entry/stop/target/position_size 未定义/未写死/缺位」作为主因（这些字段由 action 语义决定）；若认为参数不可执行，须引用**已验证价位**（止损参考带、近期高低点、MA）给出具体数值冲突。
- **对照**：r15（9 条 deep，K=3 judge，四维 + confidence 分布）为基线；数据链尽量在正常日跑（避免 AKShare 降级干扰）。
- **主指标**：① 决策分布（buy/sell/watch）；② `inaction_reason` 中「自指参数缺失」占比（确定性字符串计数，可离线复核）；
- **护栏**：`decision_grounding`/`consistency`/`report_relevance` 不得下降；价位门禁阻断数不得上升；`citation` 系列按拆报口径观察。
- **成本**：9 deep × 1 ≈ 2.4–2.6M tokens（含 judge）。

判读纪律：若指标改善伴随 dg 下降，优先解释为「为凑参数而放宽证据门槛」，不算成功；一次只动这一个变量，其余 prompt 冻结。

## 4. 关联

- `docs/evals/metrics.md` §1.20（confidence 分布口径）+ r15 阴性读数
- `docs/evals/2026-10-22-round14-收口报告.md`（数据链降级对读数的干扰）
- 价位门禁：`fix-price-gate-meta-discourse`（本轮已修，避免阻断噪声污染决策分布统计）
