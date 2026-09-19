# Design: eval-driven-contract-fixes

## Context

四处修复来自 2026-09-18 消融评估的终裁证据（grounding 扫描 8/64、A4 自然腿 2/2 误报、incident 029），互相独立、可分别落地与验证。提示词是部署产物（`deploy_prompts.py`），校验器与修复记账在 `src/finance_agent/nodes/citation_node.py` / `citation_repair.py` 一族。

## Goals / Non-Goals

- Goals：① data 论点逐断言锚定 + 反例判例进辩手提示词；② 字段解析遇同义列消歧（官方全名优先，不可消歧判 blocked）；③ 值槽类型错填判 `claim_contract_error` 进解析桶、不触发修复；④ 修复记账按 claim（新增 `value_mismatch_repaired_claims`，旧字段 deprecated 保留）。
- Non-Goals：不改辩论轮次/预算；不改四桶其余语义；不回填历史评估产物（跨口径比较靠切点标注）；不做辩手锚点的运行时强制校验回路（anchor 覆盖率指标已存在，靠观测+评估抓漂移）。

## Decisions

1. **断言级锚定靠提示词 + 观测，不做输出结构改造**（`key_arguments` 仍是 `{text, kind, anchors}`）：拆断言会改前端渲染与 B1/B2 消融口径，收益不成比例；用 anchor 覆盖率指标（已有）+ 下一轮 grounding 扫描验证收紧效果。
2. **列名消歧用显式别名映射表**（短名 → 官方全名，落在校验器的字段解析处，含负索引路径处理）：数据源列名不可控，映射表是最小且可测试的消歧点；映射缺失且多列同义时判 blocked。
3. **值槽类型校验放校验器前置**（进值比较之前）：判据 = interpretation 含环比/同比/变化词 + 数值与 field_ref 真值量级不符（如 0.6 vs 49.8）；两条信号同时满足才判契约错误，避免误伤（单一信号不足）。
4. **记账双字段过渡**：`value_mismatch_repaired`（旧，analyst all_passed）保留一轮供跨口径对照，新增 `value_mismatch_repaired_claims`；评估侧 A4 主指标立即切新口径并在 metrics.md 标切点。

## Risks / Trade-offs

- 提示词收紧可能让辩手更多标 inference（data 论点变少）→ anchor 覆盖率观测下移属预期，B1 增量率口径不受影响（无源断言本就不该算增量）。
- 别名映射表需随数据源列名漂移维护 → 消歧判 blocked 的兜底保证「宁可 blocked 不错值」，blocked 率进四桶拆报可观测。

## Migration Plan

按 tasks 顺序分别落地：②③（校验器，纯后端）→ ④（记账）→ ①（提示词 + deploy + 下一轮评估验证）。互不阻塞，可分批合入。
