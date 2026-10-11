# Proposal: complete-peer-pipeline

## Why

issue #21（同业数据管道修复与补全，ready-for-agent）在 `add-peer-comparison`（#281/#282）落地后仍余两段断点：

1. **无显式对标股时同业不可达**：`_fetch_peers` 仅在请求携带 `peer_codes` 时工作（点名对比路径），未输入对标股的分析永远没有同业对照——PRD user story 2「自动选取同行业市值 Top 5」未实现。
2. **勾稽校验告警写入 state 但从未进 LLM 上下文**：`validate_financials` 在分析师之前运行（graph 拓扑 fetch→validate→compute→analysts），`validation_warnings`（如「利润表存在较大营业外收支」）对基本面/宏观分析师不可见——PRD user story 8 未实现。

## What Changes

- 新增 `AKShareClient.fetch_industry_constituents(industry_name)`：东财行业板块成分股（代码+名称），失败返回 None（optional 降级，与既有数据源语义一致）
- `_fetch_peers` 扩展：请求未携带 `peer_codes` 且 `industry_info.industry` 可用时，自动选取同行业成分股按总市值 Top 5（排除主标的自身）作为对标股；成分股拉取失败/行业缺失/不足 1 只可比标的 → 如实无同业（`peer_financials=None`，与既有降级同语义），MUST NOT 用别的行业标的凑数
- `validation_warnings` 非空时注入基本面分析师与宏观分析师 context（机生材料段，标注「勾稽校验告警（机生，供交叉核对）」）；空列表不注入，与既有 optional 注入语义一致

## 非目标

- 相对估值/GARP 消费语义变更（`industry_pe` 与同业个股 PE 两口径并存的既有契约不动）
- prompt 模板修改（注入为 context 侧机生材料段，无需重发 Langfuse）

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: `analyst-data-sources`（ADDED 勾稽告警注入 + 行业成分股抓取；MODIFIED 同业财务数据获取补自动选取语义——「未指定对标股时不抓取」场景收窄为自动选取不可得的降级情形，新增自动选取/显式优先两场景）

## Impact

- 代码：`data/akshare_client.py`（fetch_industry_constituents）、`nodes/fetch.py`（_fetch_peers 自动选取）、`nodes/analysts.py`（FA/MA context 注入）
- 数据源：新增东财行业板块成分接口（`stock_board_industry_cons_em`），走 `_call_ak` 超时/重试/不可达 fail-fast 既有包装
- 测试：fetch 自动选取（含降级路径）、成分解析、context 注入单测；交互类判定：纯后端数据流，前端零变更 → 非 delta 免 E2E 门禁
- 关联：Closes #21（8 条 user story 全覆盖核对表见 tasks.md）
