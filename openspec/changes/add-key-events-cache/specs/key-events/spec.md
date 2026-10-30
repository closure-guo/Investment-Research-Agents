# Delta for key-events

## ADDED Requirements

### Requirement: 关键事件取数链当日缓存

`fetch_key_events` SHALL 在 WebSearch 路径成功产出结果时将结果写入当日缓存（键 `{code}:key_events`、TTL 1 天、经 `DataCache` 共享单例），并在后续同股同日调用命中缓存时直接返回缓存值，SHALL NOT 再次发起网络搜索或 LLM 提取。可缓存结果限定两类：① 提取到的事件列表；② 「验证过的空」——搜索请求成功完成、且 LLM 提取正常返回空数组。失败降级路径（搜索异常/无结果、LLM 调用异常、解析失败）落到 L3 兜底卡的调用 SHALL NOT 写缓存。`use_web_search=False` 的调用 SHALL NOT 读缓存也 SHALL NOT 写缓存。函数返回值形状 SHALL 保持不变。

#### Scenario: 同日二次调用命中缓存零网络

- GIVEN 股票 X 当日首次 `fetch_key_events` 成功经 WebSearch 取得事件并写入缓存
- WHEN 同日再次调用 `fetch_key_events(X)`
- THEN SHALL 直接返回缓存值，且不触发 ddgs 搜索与 LLM 提取（可注入的搜索/提取桩记录零次调用）

#### Scenario: 验证过的空被缓存

- GIVEN 股票 Y 当日 WebSearch 搜索成功但 LLM 提取返回空数组
- WHEN 同日再次调用 `fetch_key_events(Y)`
- THEN SHALL 返回缓存中的空列表，且不再次发起搜索与提取

#### Scenario: 失败兜底不缓存

- GIVEN 股票 Z 当日 WebSearch 搜索异常（或 LLM 提取异常），函数降级返回 L3 兜底卡
- WHEN 同日再次调用 `fetch_key_events(Z)`
- THEN SHALL 重新走完整取数链（缓存中 SHALL NOT 存在 Z 的兜底卡）

#### Scenario: 关闭 WebSearch 的调用不读不写缓存

- GIVEN 股票 X 当日已有缓存
- WHEN 以 `use_web_search=False` 调用 `fetch_key_events(X)`
- THEN SHALL 按预构建库 → 兜底的原有路径返回，且 SHALL NOT 改写缓存

### Requirement: 检索域名白名单

事件 WebSearch 的域名白名单 SHALL 包含以下 21 个域名：主流财经媒体 `caixin.com`、`stcn.com`、`cs.com.cn`、`eastmoney.com`、`hexun.com`、`sina.com.cn`、`sohu.com`、`163.com`、`ifeng.com`、`cls.cn`、`xueqiu.com`、`news.qq.com`、`investing.com`、`yicai.com`、`nbd.com.cn`；证券/研报平台 `10jqka.com`、`stockstar.com`、`cnfol.com`、`baogaoting.com`、`pedaily.cn`、`cnyes.com`。行情/报价页域名（如 `cfi.cn`、`9fzt.com`）SHALL NOT 入选。

#### Scenario: 新增域名命中白名单

- GIVEN 搜索结果含 `https://www.cls.cn/detail/…` 与 `https://xueqiu.com/s/sh601952`
- WHEN 执行域名过滤
- THEN 两条 SHALL 均被保留

#### Scenario: 行情页域名仍被滤除

- GIVEN 搜索结果含 `https://quote.cfi.cn/quote23007_601952.html`
- WHEN 执行域名过滤
- THEN 该条 SHALL 被滤除

### Requirement: 选股链事件取数携带股票名

选股日批事件装配（`_assemble_events_by_symbol`）与深度分析前时效校验（`screening_freshness_removals`）调用 `fetch_key_events` 时 SHALL 携带候选股票名构造检索查询；股票名缺失或为空时 SHALL 以纯代码查询降级且 SHALL NOT 阻断批处理。

#### Scenario: 装配传名提升召回

- GIVEN 候选 601952 的快照名为「苏垦农发」
- WHEN 日批事件装配对其取数
- THEN 检索查询 SHALL 含「苏垦农发」与「601952」

#### Scenario: 缺名降级不阻断

- GIVEN 候选 300628 的快照名为空
- WHEN 日批事件装配对其取数
- THEN SHALL 以纯代码查询降级取数，且该候选的空名 SHALL NOT 使批次失败
