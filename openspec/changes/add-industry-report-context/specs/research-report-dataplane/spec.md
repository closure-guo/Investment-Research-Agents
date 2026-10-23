# Delta for research-report-dataplane

## ADDED Requirements

### Requirement: 行业研报按行业聚合读取

系统 SHALL 提供确定性的行业研报聚合读取（`get_industry_digest`），供基本面分析师消费。

- 选取范围 SHALL 为 `research_reports.report_type='industry'` 且 `industry` 与请求行业名归一后相等的行。
- 归一 SHALL 三步：①`NFKC`；②去除首尾空白与内部空白；③剥离末尾的申万层级标识（`Ⅱ`/`Ⅲ`/`Ⅱ`、`II`/`III`）。MUST NOT 做编辑距离、子串包含或分词匹配。
- 归一后仍不相等、但命中**显式别名表**（`INDUSTRY_ALIASES`，键为个股行业名、值为研报词表名）时 SHALL 按别名选取。
- 别名表 SHALL 只收录经实测确认的词表差异项；MUST NOT 按语义猜测扩充。新增别名 SHALL 有对应测试。
- 时点约束 SHALL 为 `publish_date <= as_of`；窗口默认 90 天，由 `INDUSTRY_DIGEST_MAX_AGE_DAYS` 覆盖。
- 条数默认 8 篇，由 `INDUSTRY_DIGEST_MAX_REPORTS` 覆盖，按 `publish_date` 降序。
- 输出 SHALL 含 `industry` / `status` / `report_count` / `brokers` / `latest_report_date` / `age_days` / `documents`（每条含 report_key、title、org、analyst、publish_date、depth_class）。`status ∈ {ok, stale, unmatched}`。
- 聚合 MUST NOT 调用 LLM。
- 请求行业名为空 SHALL 返回 None。
- 库内存在该行业记录、窗口内有数据 → `status='ok'`。
- 窗口内 0 篇但库内存在该行业历史记录 → `status='stale'`，含 `latest_report_date` 与 `age_days`，`documents` 为空；MUST NOT 列出窗口外的陈旧标题。
- 行业名归一与别名均未命中 → `status='unmatched'`，`documents` 为空；MUST NOT 返回 None 冒充「无研报」（两种状态在消费端必须可区分）。
- 库内完全没有 industry 记录 → 返回 None。
- 该行业在 `as_of` 之前没有任何记录（仅有未来研报）→ 返回 None（时点正确性优先，不得以未来研报充数）。
- 读库异常 SHALL fail-open 返回 None，MUST NOT 阻断分析管线。

#### Scenario: 同行业多篇聚合

- GIVEN 库内有 3 篇 industry='电池'、publish_date 为 2026-10-20 / 19 / 18 的行业研报
- WHEN 以 industry='电池'、as_of='2026-10-21' 读取
- THEN report_count SHALL 为 3
- AND documents SHALL 按发布日期降序排列
- AND latest_report_date SHALL 为 2026-10-20

#### Scenario: 未来研报不入聚合

- GIVEN 库内存在 publish_date='2026-10-25' 的电池行业研报
- WHEN 以 as_of='2026-10-21' 读取
- THEN 该篇 MUST NOT 出现在 documents 中
- AND report_count MUST NOT 计入该篇

#### Scenario: 层级后缀归一命中

- GIVEN 库内研报行业为「白酒Ⅱ」，个股行业为「白酒」
- WHEN 读取执行
- THEN 归一剥离层级标识后相等
- AND 结果 status SHALL 为 ok 且命中「白酒Ⅱ」的研报

#### Scenario: 别名表命中

- GIVEN 个股行业为「多元化保险」且别名表登记其对应「保险」
- WHEN 读取执行
- THEN 结果 SHALL 命中研报行业「保险Ⅱ」（层级剥离后为「保险」）

#### Scenario: 口径未覆盖显式化

- GIVEN 个股行业为「通信服务」，而研报词表只有「通信设备」
- WHEN 读取执行
- THEN 结果 status SHALL 为 unmatched
- AND MUST NOT 返回 None 冒充「无研报」
- AND MUST NOT 返回其他行业的研报充数

#### Scenario: 断更显式化

- GIVEN 库内最新一篇电池行业研报为 2026-06-01
- WHEN 以 as_of='2026-10-21'、默认 90 天窗口读取
- THEN 结果 SHALL 带 stale 标注并含 age_days
- AND documents SHALL 为空

#### Scenario: 空库为 None

- GIVEN 库内没有任何 industry 类型研报
- WHEN 读取执行
- THEN 结果 SHALL 为 None

### Requirement: 行业研报注入基本面分析师上下文

系统 SHALL 在构建 fundamental_analyst 的 LLM context 时，若 state 存在 `industry_report_digest`，注入「同行业研报」段。

- 段头 SHALL 标注 state 键与数据形态（仅元数据、未读取正文）。
- 段 SHALL 附防锚定条款：标题与机构立场不构成结论依据。
- `status='unmatched'` SHALL 渲染为一句口径说明（个股行业名 + 「研报库无对应行业口径」），MUST NOT 表述为「无研报」。
- `status='stale'` SHALL 渲染断更说明（最新日期 + 距今天数），MUST NOT 列出窗口外标题。
- digest 为 None 时 MUST NOT 出现该段，也 MUST NOT 生成占位文本。
- 该段 SHALL NOT 注入宏观、技术面、舆情分析师上下文。
- 段文本 SHALL 受硬上限约束（默认 8 条、逐条标题截断），且 MUST NOT 改变既有记忆块裁剪顺序（analyst-context-budget）。

#### Scenario: 有数据注入

- GIVEN state 含 industry_report_digest（3 篇电池行业研报）
- WHEN 构建基本面分析师上下文
- THEN context SHALL 含「同行业研报」段
- AND 段内 SHALL 含「仅元数据」
- AND 段内 SHALL 含防锚定条款

#### Scenario: 无数据不占位

- GIVEN state 不含 industry_report_digest
- WHEN 构建基本面分析师上下文
- THEN context MUST NOT 含「同行业研报」

#### Scenario: 其他分析师不消费

- GIVEN state 含 industry_report_digest
- WHEN 构建宏观、技术面、舆情分析师上下文
- THEN 均 MUST NOT 含「同行业研报」

#### Scenario: 口径未覆盖如实说明

- GIVEN digest 的 status 为 unmatched，个股行业为「通信服务」
- WHEN 构建基本面分析师上下文
- THEN 段内 SHALL 出现「通信服务」与「研报库无对应行业口径」
- AND MUST NOT 出现「无研报」字样

### Requirement: 行业研报标题进入引用回声源

系统 SHALL 将 `industry_report_digest` 的标题并入 citation 回声源集合，使文本 claim 可判 PASS(echo)，与策略观点聚合的既有处理一致。

#### Scenario: 标题可回

- GIVEN state 含 industry_report_digest，标题为「动力电池行业跟踪」
- WHEN 引用校验构建回声源集合
- THEN 集合 SHALL 含该标题
