# add-anchor-value-grounding 人工验证报告（issue #242 断点 2）

- 日期：2026-10-10
- Delta：`add-anchor-value-grounding`（openspec/changes/add-anchor-value-grounding/）
- 范围：辩论锚点确定性校验升级——matched_via 解析途径记录 / 数值溯源 value_mismatch / field 形态回声错位 field_ref_echo_only
- 验证性质：零 LLM 确定性校验——构造 state 直测 + 全量回归，无需实跑分析

## 1. 光大 601818 三形态复核

归一探针实证（`_norm_text`）：完整舆情标题「光大银行：中报净利润同比-24.01%，下滑扩大」与锚 `fundamental.中报净利润同比` **双向归一子串均不命中**——原案例属 mode C（unresolved 已如实记录，fail-open 且零消费面），并非回声误判。升级后三形态实测输出：

| 形态 | 构造 | 实测结果 |
|---|---|---|
| **C（原案例）** | 快照同比暂缺 + 完整标题 + inference 锚 `fundamental.中报净利润同比` | `anchor_statuses=["unresolved"]`、`matched_via=[""]`、stats `unresolved=1`——校验如实记录，消费面由断点 3 delta（FM 审批上下文告警）承接 |
| **A（相邻变体）** | 短标题「中报净利润同比」（归一后为锚点子串）回声命中 | `matched_via=["echo"]`、`echo_only_field_refs=["fundamental.中报净利润同比"]`、stats `field_ref_echo_only=1`——「声明指向结构化字段、证据来自文本」的错位首次可见 |
| **B（数值缺口）** | `fundamental.中报净利润同比=-0.2401` field 命中 + 文本「下滑 8.06%」 | `status="value_mismatch"`、`anchored=true`——文本数字与锚定值不可对上的可追溯性缺口 |

fail-open 实证：校验后 state 逐键不变（`state_untouched: True`），不触发重跑/阻断/路由变化。

## 2. 误报防线（D4）实测

- 「MA5 上穿 MA20，R1 回应 2024 年报显示盈利改善」锚 MA 值 2.0 → `resolved`（标识符/年份 token 排除）
- 「中报净利同比下滑 24.01%」锚 -0.2401 → `resolved`（绝对值 + ×100 形态匹配）
- 「盈利能力恶化」（无数字）→ `resolved`（无数值 token 不查）
- 回声命中锚点不在数值溯源范围（子串命中即溯源）

## 3. 自动化验证汇总

- `pytest tests/nodes/test_debate_anchors.py`：26 passed（新增 10 用例 + 2 契约钉随 delta 更新，先红后绿）
- 消费方套件（test_debate_anchor_wiring / test_ablation / test_task / test_extract / causal_ablation）：132 passed
- 全量 `pytest -m "not live"`：4528 passed, 0 failed
- `ruff check` / `ruff format --check` / `mypy`：零错误
- `openspec validate add-anchor-value-grounding --strict`：valid
- spec 契约钉更新说明：record 键集 +`matched_via`/`echo_only_field_refs`、stats +`value_mismatch`/`field_ref_echo_only`（delta MODIFIED 块内显式声明）

## 4. 范围外

- FM 审批上下文消费本 delta 产出的告警信号 → 断点 3 delta `add-fm-grounding-surface`
- 锚点门禁化（路由/重试变更）→ 观测若干轮误报率后再评估（incident 026 纪律：先归因后处置）
