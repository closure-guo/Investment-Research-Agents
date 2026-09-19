## Why

analysts context 提示 LLM「field_ref 前缀 `derived.`」，而 state 根键是 `derived_series`——**前缀契约不一致**。此前「常用派生值」节因 `derived_series` 被图静默丢弃（incident 027 同型，已随 delta `close-citation-coverage-gaps` 修复）而从未渲染，该提示是死文案；图通道修好后该节首次真正渲染，LLM 若照提示写 `derived.chg_5d`，**正确数值会被判 `path_unresolvable` 误 FAIL**（2026-09-14 实测：同值 `derived.chg_5d` FAIL / `derived_series.chg_5d` PASS）。

这是必须随修复一起收口的一致性缺口：新增可引用路径却留下一个会制造误报的前缀。

## What Changes

- **根键别名归一**：`_ROOT_ALIASES = {"derived": "derived_series"}`，解析（`_resolve_field_ref`）与计算型注册表查找（`_verify_computational`）统一应用；两种写法等价，其余路径语义不变。
- **提示文案改规范前缀**：analysts context 改为 `field_ref 前缀 derived_series.（如 derived_series.chg_5d）`——以规范形态减少歧义，别名作为容忍层保留。
- 归一口径并入既有 requirement「路径形态归一」（SHALL 双向幂等，不改负索引/`[N]` 语义）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `citation-verification`: 「路径形态归一」增加根键别名归一条款（`derived` → `derived_series`）+ 新场景。

## Impact

无产品行为变化（不新增数据、不改打分口径）；仅提升解析容忍度并消除一处会制造误报的前缀契约缺口。回归：`tests/test_citation.py::TestDerivedRootAlias` + `TestComputationalRegistryCoverage::test_alias_root_recomputes`（先红后绿）。
