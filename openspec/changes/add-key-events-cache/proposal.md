# Proposal: add-key-events-cache

## Why

2026-10-30 生产实测：main 池 35 只中 31 只（89%）的 L3 粗筛理由含「事件数据不可用」。根因三层（均有容器内实证）：

1. **选股链取事件无缓存**：15:40 批 100 只候选 × 16~26s/只（一次 ddgs 搜索 + 一次 LLM 提取）≈ 30~40 分钟/天，16:22 时效校验对同一批再裸跑一遍；
2. **检索域名白名单过窄**：财联社（cls.cn）、雪球、腾讯新闻、investing.com 均不在 15 域白名单内——601952 实测 10 条搜索结果被砍到 3 条，且其中一条是融资融券数据页；
3. **选股链查询不带股票名**：`_assemble_events_by_symbol` 只传代码（名字在批内现成可得），601952 实测带名查询白名单内保留 4 条、纯代码查询仅 1 条。

后果不只是文案：16:22 时效校验靠事件清单发现「新发重大事件」排雷，拿到的全是中性兜底卡 = 排雷事实上失明。

## What Changes

- `fetch_key_events` 内建当日缓存（复用 `DataCache` 共享单例，key `{code}:key_events`、TTL 1 天，与深析 fetch 节点既有缓存语义对齐）：**「验证过的空」（搜索与提取均正常完成但无 L1/L2 级事件）与「取到的事件」缓存；「失败兜底」（搜索/提取异常落到 L3 兜底卡）不缓存**——瞬时故障不毒化全天；`use_web_search=False` 路径不读不写缓存（保持零网络契约）；返回值形状不变，所有消费方零改动。
- 白名单扩 6 域：`cls.cn`、`xueqiu.com`、`news.qq.com`、`investing.com`、`yicai.com`、`nbd.com.cn`；行情页域名（cfi.cn、9fzt.com 等）不入选。
- 选股链两个取数点（`_assemble_events_by_symbol`、`screening_freshness_removals`）携带股票名构造查询；名字缺失退化为纯代码查询、不阻断。

**不改动**：L2→L1→L3 三级回退结构、提取 prompt 口径（L3 边缘信息排除——范围裁决时明确不动）、兜底卡文案、深析 fetch 节点现有缓存层。

## Capabilities

- **New Capabilities**: `key-events`（关键非财务事件获取——此前无任何 spec 覆盖取数行为，本变更首次立契约）

## Impact

- 代码：`src/finance_agent/events/`（config 白名单、pipeline 缓存与签名不变传名）、`src/finance_agent/screening/pipeline.py::_assemble_events_by_symbol`、`src/finance_agent/screening/freshness.py`
- 消费方兼容：triage prompt / 时效校验 / 深析 fetch 节点 / 引用校验回声源（`key_events` 通道）——返回形状不变，全部无需改动
- 存储：复用既有 `DataCache` 共享单例（cache.db），零新存储文件
- 运维验证（部署后次日）：15:40 批事件装配耗时、「事件数据不可用」占比两个读数对比
- 纯后端数据管道：不涉及 UI/SSE/会话/状态流转，不触发 E2E 门禁
