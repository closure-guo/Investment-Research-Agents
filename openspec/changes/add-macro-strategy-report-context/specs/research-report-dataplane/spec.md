## ADDED Requirements

### Requirement: 策略与宏观观点抽取

投喂研报的结构化抽取 SHALL 为策略/宏观类报告输出行业观点与市场观点，不得因缺少个股条目而丢弃观点信息。

- 新增 `sector_views[]`：每条含 `industry`、`direction ∈ {看多, 中性, 看空, 未知}`，可选 `rationale` 与 `source_page`。
- 新增 `market_view`：含可选 `sentiment`、`positioning`、`key_catalysts`；只抽原文明确表述，MUST NOT 推断仓位或情绪。
- 缺失新字段的旧抽取 SHALL 视为空，MUST NOT 要求重抽或阻断合并。
- 抽取 MUST NOT 调用 LLM 之外的规则做二次发挥；仍遵循「只抽原文」。

#### Scenario: 策略报告产出行业观点

- **GIVEN** 一份策略报告正文明确写着「看好银行、电池与部分工业疲弱」
- **WHEN** 抽取运行
- **THEN** `sector_views` SHALL 含银行（看多）与电池/工业（看空或中性）
- **AND** `direction` SHALL 取自原文语义，无法判断时为「未知」

#### Scenario: 仓位表述只抽不推

- **GIVEN** 原文写「外资仓位较 6 月减轻」
- **WHEN** 抽取运行
- **THEN** `market_view.positioning` SHALL 记录该表述
- **AND** MUST NOT 补写原文没有的仓位百分比或方向

#### Scenario: 旧抽取兼容

- **GIVEN** 一份没有 `sector_views` / `market_view` 的存量抽取
- **WHEN** 聚合或注入运行
- **THEN** SHALL 把缺失字段视为空
- **AND** MUST NOT 报错或要求重抽

### Requirement: 策略报告个股清单边界

策略/宏观报告的 `stocks[]` SHALL 只包含正文中真正被讨论、带个股论据的标的；附录、免责声明或「全覆盖评级表」中的条目 MUST NOT 进入 `stocks[]`。

- 判断标准：该标的在正文中有独立论述（观点、逻辑、评级依据、目标价、盈利预测）之一。
- 仅出现在披露表中的「12 个月评级 + 当日收盘价」SHALL 视为覆盖披露，不是本篇报告的个股结论。
- 抽取器无法判断时 SHALL 宁可为空，MUST NOT 为凑数而列出。
- 如未来需要保留披露表评级，SHALL 使用独立字段（如 `disclosure_ratings[]`），不得复用 `stocks[]`。

#### Scenario: 披露表条目不入个股清单

- **GIVEN** 一份策略报告正文只讨论行业，附录列出 8 只 A 股评级与当日价格
- **WHEN** 抽取运行
- **THEN** `stocks[]` SHALL 为空
- **AND** MUST NOT 把附录条目当作个股覆盖写入

#### Scenario: 正文讨论的个股可入清单

- **GIVEN** 报告正文用一段分析某只个股并给出评级依据
- **WHEN** 抽取运行
- **THEN** 该标的 SHALL 进入 `stocks[]`
- **AND** SHALL 携带正文来源页或 null

### Requirement: 策略观点聚合读取

系统 SHALL 提供确定性的策略/宏观观点聚合读取，供宏观分析师消费。

- 选取范围 SHALL 为 `report_type ∈ {策略报告, 宏观研究}` 的抽取结果。
- 默认取最近 30 天内、按报告日期降序的前 3 篇；阈值 SHALL 可配置。
- 聚合 SHALL 输出机构、日期、行业观点方向分布与论据、核心逻辑、催化剂、风险、分歧点；MUST NOT 调用 LLM。
- 无记录时 SHALL 返回 None；最新一篇超过 30 天时 SHALL 返回 None 并记 `stale`，MUST NOT 以陈旧观点冒充当前观点。
- 聚合结果 SHALL 受硬上限约束（行业数、每行业论据数、各清单条数），超出截断并标注。

#### Scenario: 多篇聚合并标出分歧

- **GIVEN** 两篇 30 天内的策略报告，一篇看多银行、一篇看空银行
- **WHEN** 聚合读取运行
- **THEN** `sector_views` SHALL 含银行组的方向分布
- **AND** `disagreements` SHALL 记录该行业存在方向冲突

#### Scenario: 陈旧观点不注入

- **GIVEN** 库中最新策略报告日期距今 45 天
- **WHEN** 聚合读取运行
- **THEN** SHALL 返回 None 并记 `stale`
- **AND** 宏观上下文 MUST NOT 出现该观点

#### Scenario: 无策略报告为空态

- **GIVEN** 库中没有任何策略/宏观抽取
- **WHEN** 聚合读取运行
- **THEN** SHALL 返回 None
- **AND** MUST NOT 生成空段或占位文本
