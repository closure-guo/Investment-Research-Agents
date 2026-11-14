# Design: enforce-decision-contract-at-execution

## Approach

### 数据流（修复后）

```
DecisionOutput (models.py: trigger_high/exit_schedule/position_size="light")
  │ 落库 decision_log（现仅 entry/stop/target/confidence/position_size REAL）
  │ rationale_snapshot JSON（全量冻结，现有机制）
  ▼
_create_signals_from_decisions → _decision_payload  ← 本 delta 改造点①
  │ 从 snapshot 解析：trigger_high / position_tier / exit 首批比例
  │ （entry_price 走既有列）
  ▼
Signal（execution/signals.py）                      ← 契约扩展点②
  │ 新字段: trigger_high: float|None, position_tier: str|None,
  │         exit_first_batch_ratio: float|None
  ▼
_plan_long_entries / _plan_flat_exits（driver.py）   ← 行为改造点③
  │ ①触发门: bar.close vs trigger → TRIGGER_NOT_MET 留痕
  │ ②档位→权重: 下沿锚 10/30/50%，clip 保留；缺档退化等权
  │ ③exit 首批: 首日卖 min(比例,持仓) 整手；首批状态持久化
  ▼
执行摘要 plan_deviation 段                           ← 新增④
```

### 关键设计决策

1. **档位取下沿锚**（light=10%/moderate=30%/heavy=50%）：与 `trader.md:117` 档位区间对齐取下沿——方向保守（宁小勿大），且有生产先例（600780 方案 FM 批准文本明文「light 档下沿约 10%」）。
2. **trigger 判定用 bar.close**：与决策层「收盘价确认」语义一致（江天方案明文「放量站上 MA20 为触发」的价格分量）。**放量分量不实现**——prompt 层语义（量能未放大即放弃）无结构化字段，Phase 1 price-only，简化在本设计披露；靠 plan_deviation 暴露偏差。
3. **约束字段从 rationale_snapshot 解析而非 decision_log 加列**：snapshot 是全量冻结 JSON（`_decision_payload` 已在此解析 action），不改表结构、无迁移成本；决策→信号是每日幂等转换（`signal_id={symbol}:{trade_date}`），解析失败按脏数据跳过留痕的既有模式可复用。若实现期发现 snapshot 字段名不稳定，再立 `decision_log` 加列的后续 delta。
4. **exit 首批状态持久化**：首批已执行标志存 execution 库（复用 signals 表加状态列或独立小表，实现期按 `execution/store.py` 现有模式定）；以 decision_id 为键，天然幂等。
5. **退化方向取保守**：schedule 不可解析 → 全额退出（= 既有行为，风险不升高）；档位缺失 → 等权（= 既有行为）。所有退化必须留痕——本 delta 的原则是「行为可退化，账不可假」。
6. **不改 prompt**：`entry_cap`（成交硬上限）在现网三单里只存在于 reasoning 文本。本期不扩 trader 输出 schema（改 prompt 须重推 deploy_prompts.py，成本高且与执行链修复解耦）；超限成交靠 R5 对账留痕暴露，若频发再立 prompt delta。

### 与 #593 的关系

本 delta 是 #593 的规格化。#593 证据三单分别对应 R2（300927 违规建仓）、R3（600780 档位 2 倍违约）、R4（601066 分批压扁）；R1 是三者共同的数据前提；R5 是防再犯的观测面。实施完成后 #593 附验证记录核关。

## Alternatives Considered

- **方案 A：trader 输出 schema 加 `entry_cap` 结构化字段并在执行层消费** —— 改 prompt 契约 + 重推部署 + 重跑 eval 门禁，改动面横跨决策与执行两层；本期拒绝，留作后续独立 delta（若 R5 显示超限成交频发则升级）。
- **方案 B：executor 直接解析报告 Markdown 文本** —— 无结构化契约、脆、不可测；拒绝。
- **方案 C：只加对账告警不改行为（观测先行）** —— 不解决违约建仓本身，亏损继续发生；拒绝。#593 的损失已是行为缺陷的直接证据，观测面（R5）与行为门（R2-R4）本期同批交付。

## Risks

- **风险 1：trigger 判定口径与决策语义偏差**（收盘确认 vs 盘中突破）。对策：R2 固定为 bar.close 口径并在留痕中同时记录 trigger 与实际收盘，偏差可审计；放量分量缺失已披露。
- **风险 2：档位锚（10/30/50）与未来 prompt 档位表漂移**。对策：design 明确锚值来源 `trader.md:117`；若 prompt 档位表变更，先改 prompt 契约场景（agent-prompt-contracts 已要求模板含档位区间），再同步本映射，两处同批评审。
- **风险 3：exit 首批整手取整后剩余股数 < 100 的尾差**（如 9,100×70%=6,370 → 整手 6,300，剩 2,800 正常；若持仓 120 股×70%=84 → 整手取整为 0/100 的边界）。对策：实现期钉死「首批 = floor(持仓×比例/100)×100，首批为 0 时视为未执行并顺延」并配边界单测。
- **风险 4：存量 open 信号无新字段**（本 delta 上线前已入池的 ~50 条 open 预测）。对策：R1 退化路径覆盖——无 trigger 不设门、无档位走等权，行为与今日完全一致，零迁移。
