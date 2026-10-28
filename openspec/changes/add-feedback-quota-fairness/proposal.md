# Proposal: add-feedback-quota-fairness

## Why

跨会话回喂载荷（`explored_factors.json`）的分档分配存在**确定性饿死**：`factors/feedback.py` 的 `build_payload` 把 rejected 档按闸号升序排列后受总量 `limit=80` 截断——闸②（WEAK_ICIR，累计 395 条）必然占满 rejected 档全部出场位，闸④（REDUNDANT_WITH_POOL，累计 84 条）**累计 0 条出场**。10-27 供给链审计已将此立为盲点：生成侧永远收不到「与池冗余」的拒收经验，继续量产同类量价因子（量价族内卷）。同构问题存在于 untranslatable 档的失败类别（stage）维度：现有保底配额只做按日轮转，单一 stage（如缺横截面算子，389 条最大桶）仍可垄断配额，残差×反转族 5 轮重提零传导即其表现。此缺陷可由代码+计数直接复现，不依赖 #482 判效读数。

## What Changes

- rejected 档引入**每闸保底配额**（出现过拒收的闸口每闸至少 N 条，默认 5，可配置）；保底之外的余量按各闸拒收量占比分配；保底未用满的空位归还总体。
- untranslatable 档在既有按日轮转之上增加**失败类别（stage）最低代表性**：单一 stage 不得独占该档配额。
- 载荷新增配额分配读数（每档/每类别出场条数），供监控与判效核对。
- 既有语义零回退：admitted 全量在前、按规范化表达式去重、总量 `limit` 上界、`contract` 块与 `gate_rejections` 聚合（feed-dsl-contract-to-factor-generator）照常存在；生成侧消费端零改动（载荷结构只增不改）。

## Capabilities

- **Modified Capabilities**: 无
- **New Capabilities**: 无（在既有 capability `factor-proposal` 下 ADDED 一条新需求——主规范现无回喂清单分配的任何条目，该行为此前未进 spec）

## Impact

- `src/finance_agent/factors/feedback.py`（`build_payload` 分配逻辑 + 配额常量）
- `tests/`（分配器单测 + 既有语义回归）
- 消费端 `tests/scripts/spike/explored_feedback.py` 零改动；`factor-research-monitor` 若展示回喂读数可后续接配额读数（非本 delta 范围）
- 依赖判效：本 delta 与 #482（契约回喂）变量正交（一个管「喂什么清单」，一个管「契约段」），但**实施排期仍建议在 10-31 判效读数之后**，避免同期双变量归因混淆
