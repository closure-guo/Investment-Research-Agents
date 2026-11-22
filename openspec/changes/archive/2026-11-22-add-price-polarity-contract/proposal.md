# Proposal: add-price-polarity-contract

## Why

issue #606（open，P1）：`sell_type=exit` 决策的三价申报字段（entry/stop/target）存在**两套互斥极性混用**——A 型 short 惯例（stop 在 entry 上方）、B 型 exit 惯例（stop 在 entry 下方=清仓位、target 在上方=反弹减仓带）、C 型叙事与字段打架。FM 已在理由里自曝「三价字段方向错置整体作废、按文字口径执行」，但错置值仍原样入库，记账更正没有回写机制。11-19/20 三批 30 条实证，每夜 ~30 条持续积累。

根因：#204（sell exit/short 分型）引入 exit 语义时未同步定义三价字段的极性契约；`nodes/validate.py` 的价格关系校验按 `direction`（sell→short）强推 short 极性，exit 叙事价位被镜像改写；`track_record/ingest.py` 冻结 `declared_prices` 时不带极性声明。**字段语义无契约，展示/执行/结算只能靠叙事兜底**（#593 的孪生问题）。

## What Changes

- **新增三价字段极性契约**：sell 决策按 `sell_type` 分型声明极性——`exit` → exit 惯例（stop<entry<target），`short` → short 惯例（stop>entry>target）；buy 一律 long 惯例
- 价位 sanity 校验**按分型极性**校验价格关系，SHALL NOT 对 exit 决策套用 short 极性镜像改写（现状 validate.py:307-314 的行为修正）
- 极性声明随 `rationale_snapshot.declared_prices` 冻结落库；消费方（前端展示、执行引擎）按声明极性解释，SHALL NOT 猜测
- FM「按文字口径执行」类裁决提供**记账口径更正回写机制**（回写 declared_prices + 修正来源可观测）
- 存量混用记录（#606 的 30 条）**不追溯改写**（append-only 先例），展示层可辨识「口径未声明」

**非目标（待拍板，另行立项）**：`sell=exit` 观点的 direction 映射与记分卡结算语义（#606 影响分析第 3 条——执行叙事成功与记分卡得分系统性反向）。这属于统计语义变更，与字段层契约分离拍板。

## Capabilities

- **New Capabilities**: 无（契约并入既有 capability）
- **Modified Capabilities**: decision-outcome（ADDED requirement：三价字段极性契约）

## Impact

- `src/finance_agent/nodes/validate.py`：价格关系校验按 sell_type 分型（现 :307-314 按 direction）
- `src/finance_agent/outcome/track_record/ingest.py`：冻结 declared_prices 带 polarity 声明（现 :103）
- `src/finance_agent/models.py`：TradeDecision 极性声明派生（:211 `sell_type` 已有）
- 前端 track-record 观点日志/详情：stop/target 列按声明极性标注解释；存量行标「口径未声明」
- 交互类判别：涉及前端 UI → 走 §3 完整管线（E2E 门禁 + 人工验证）
- 存量数据：predictions 表存量行 polarity 为 NULL，展示层标注，不追溯
