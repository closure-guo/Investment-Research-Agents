# Tasks: add-anchor-value-grounding

## 1. 确定性校验升级（TDD 先红）

- [x] 1.1 失败测试：检查记录新增 `matched_via`（与 anchors 平行：data 命中=field_ref、event 命中=echo、inference 两段式各记其途、未命中=""）
- [x] 1.2 失败测试：data 锚 field 命中 + 文本数值可溯源（×100 百分数形态 0.1568↔15.68%、绝对值形态 -24.01↔「下滑 24.01%」）→ status 仍 resolved
- [x] 1.3 失败测试：data/inference 锚 field 命中 + 文本含数值 token 但无一匹配 → status=value_mismatch（anchored 仍 true）
- [x] 1.4 失败测试：标识符形态不触发（「MA5 上穿 MA20」锚 MA 值；R1；2024Q1）、孤立年份不触发（「2024 年报显示…」）、文本无数字不触发
- [x] 1.5 失败测试：inference field 形态锚（首段=state 根键）仅回声命中 → 记录 `echo_only_field_refs` 收录该锚 + stats `field_ref_echo_only` 计数；event 回声锚不含点不计
- [x] 1.6 失败测试（契约钉随 delta 更新）：record 键集 +`matched_via`/`echo_only_field_refs`；stats +`value_mismatch`/`field_ref_echo_only`
- [x] 1.7 实现 debate_anchors.py：`matched_via` / 数值溯源（D3 宽容匹配 + D4 token 提取）/ `echo_only_field_refs` / stats 两新桶

## 2. 验证与回归

- [x] 2.1 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [x] 2.2 openspec validate --strict 通过
- [x] 2.3 光大场景复核：构造「快照同比暂缺 + inference 锚 fundamental.中报净利润同比」实证锚判 unresolved 且 stats 如实计数、fail-open（路由零变化）；相邻变体（短标题回声命中）实证 `field_ref_echo_only` 触发 → tests/validation 人工验证报告
- [x] 2.4 消费面承接确认：add-fm-grounding-surface delta 已立项（断点 3，同 issue #242）
