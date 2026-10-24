# Proposal: refine-harvest-errors-caliber

## Why

收割摘要的 `errors` 桶混着两类性质完全不同的条目：**已知不可翻译/转译失败的占位**（占绝大多数，且已被 `untranslatable`/`transcription_errors` 单列计数）与**真实预检失败**。运维扫一眼 `errors=38` 容易误读成「38 条异常」——实际可能 35 条是已知拒译留痕（issue #465）。spec 钉死「转译失败/不可翻译/预检失败 SHALL 逐条计入 errors 桶留痕」（留痕语义不能动），因此修读数不修流。

## What Changes

- 收割摘要新增 `errors_breakdown: {"placeholders": N, "real": M}`：`placeholders` = 本批不可翻译 + 转译失败占位数，`real` = `errors − placeholders`（真实预检失败，正常为 0）；`errors` 总数与「计入 errors 桶留痕」语义逐字不变。

## Capabilities

- **Modified Capabilities**: factor-proposal（新增「收割摘要读数口径」需求）

## Impact

- `src/finance_agent/factors/harvest.py`（摘要构造）
- `tests/factors/test_harvest.py`（断言新增键；stub 场景覆盖 real>0）
- 消费方只读透传（ops API / 前端展示），无需改动
