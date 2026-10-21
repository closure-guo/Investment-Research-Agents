# Design: add-macro-strategy-report-context

## Approach

### 抽取契约

`report_extraction.md` 在现有字段之外新增：

```json
{
  "sector_views": [
    {"industry": "银行", "direction": "看多", "rationale": "净息差企稳、股息率利差高", "source_page": 2}
  ],
  "market_view": {
    "sentiment": "谨慎",
    "positioning": "外资仓位较 6 月减轻",
    "key_catalysts": ["政策响应", "国企资金买入 A 股"]
  }
}
```

- `sector_views` 每条必填 `industry` 与 `direction ∈ {看多, 中性, 看空, 未知}`，`rationale`/`source_page` 可空。
- `market_view` 三个子字段均可空；`positioning` 只抽原文明确表述，不推断。
- `stocks[]` 的硬规则补充：只放正文中带个股论据的标的；附录/免责声明里的「全覆盖评级表」MUST NOT 进入。抽取器无法判断时宁可为空。
- 兼容旧抽取：缺失新字段视为空，不要求重抽。

### 策略观点聚合

`get_strategy_digest()`（纯读库、确定性）：

1. 取 `report_extractions` 中 `report_type ∈ {策略报告, 宏观研究}` 的记录。
2. 过滤 `report_date` 在最近 30 天内；按 `report_date` 降序取前 3 篇（可配置 `STRATEGY_DIGEST_MAX_REPORTS` / `STRATEGY_DIGEST_MAX_AGE_DAYS`）。
3. 聚合：
   - `brokers` / `dates` / `documents`；
   - `sector_views` 按 `industry` 分组，统计方向分布与论据（去重，单组最多 3 条）；同一行业方向冲突时记入 `disagreements`；
   - `core_logic` / `catalysts` / `risks` 各取最多 3 条（按报告日期新→旧，去重）；
   - `summary` 不拼接全文，只保留每篇的 200 字摘要，总长度受上限约束。
4. 无记录返回 `None`；最新一篇超过 30 天返回 `None` 并记 `stale`（可配置为「注入但标注陈旧」）。

**MUST NOT** 调用 LLM；聚合只做分组、去重、截断。

### 注入

- `fetch` 节点新增 `state["strategy_report_digest"] = get_strategy_digest()`；失败降级为 `None`，不阻断管线。
- `_build_macro_context` 在宏观指标段之后追加「卖方策略观点（state 键 strategy_report_digest）」；digest 为空则不追加。
- 段首声明：机构、日期、样本数；段尾附防锚定条款：卖方观点有立场与覆盖偏差，不得替代宏观数据推理，数据型 claim 仍须引用 `macro_indicators`。
- `_build_fundamental_context` MUST NOT 读取该键；基本面仍只消费个股研报、一致预期与手工投喂的个股合并。

### 引用回声

`collect_text_sources` 新增 digest 中的 `sector_views[*].rationale`、`core_logic`、`catalysts`、`risks`、`summary`（以及 broker/date 拼接文本）。这样宏观分析师的文本 claim 只要与原文措辞一致，就能命中 PASS(echo)，不会被记成 UNVERIFIABLE。数值型 claim 不走回声，仍须 field_ref 指向 `macro_indicators`。

### 提示词

`macro_analyst.md` 新增：

- 输入清单增加「卖方策略观点」；
- 消费条款：可引用其行业偏好/催化剂/风险，但 MUST 与宏观指标交叉；不得把卖方评级当宏观事实；
- 输出 claims 的 `field_ref` 规则不变：宏观数据仍指向 `macro_indicators`；文本 claim 的 `field_ref` 可写 digest 条目原文。

修改后执行 `uv run python scripts/deploy_prompts.py`，否则 eval 门禁拒绝运行。

## Alternatives Considered

- **把策略报告按行业映射到个股（用行业代码匹配）**：否决。策略观点是行业/风格判断，映射到个股会把「看好银行」变成对某只城商行的买入理由，属于错误归因。
- **把策略观点同时注入基本面分析师**：否决。基本面需要的是个股证据；策略观点只会增加锚定与上下文体积。
- **让 LLM 汇总多篇策略报告**：否决。v1 用确定性聚合，避免引入不可复算的二次观点；LLM 只做单篇抽取。
- **将附录披露表评级作为个股信号**：否决。见 `stocks[]` 边界；如未来需要，另立「机构覆盖度」能力，不混入个股论证。

## Risks

- **单篇主导**：只投喂了一篇 UBS 时，digest 会被单一机构观点主导。对策：段首明确机构与样本数；少于 2 篇时标注「单机构观点」。
- **观点陈旧**：30 天阈值对快速变化的市场仍可能过宽。对策：段首显示报告日期；超过 14 天在文本中加「已发布 N 天」提示（不阻断）。
- **上下文膨胀**：3 篇 × 5 条论据仍可能偏长。对策：硬上限（sector_views 最多 6 个行业、每行业 3 条论据；其余各 3 条），超出截断并标注。
- **抽取为空**：模型可能对策略报告只给 summary 不给 sector_views。对策：digest 允许只有 core_logic/catalysts；全部为空则不注入；抽样验证 prompt 有效性。
- **锚定**：宏观分析师可能直接复述卖方观点。对策：prompt 要求与 `macro_indicators` 交叉，并在 claims 里区分数据与观点；eval 中观察宏观段是否出现无指标支撑的结论。
