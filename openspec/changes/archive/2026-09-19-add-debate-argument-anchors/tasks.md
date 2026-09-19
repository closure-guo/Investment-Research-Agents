## 1. 模型与解析（TDD 先红后绿）

- [x] 1.1 红灯：`tests/nodes/test_debate_arguments.py`——结构化项解析 / `text` 为空抛验证异常 / 裸字符串列表 → `unspecified` / `kind` 缺失或非法 → `unspecified` 不抛 / `rebuttal_to` 位置语义不变
- [x] 1.2 `models.py` 新增 `DebateArgument`；`DebateMessage.key_arguments: list[DebateArgument]` + `model_validator(mode="before")` 兼容旧格式
- [x] 1.3 `nodes/_llm_utils.py` TESTING stub 改为结构化项；`nodes/debate.py` / `nodes/risk.py` 历史渲染只取 `text`（既有交锋覆盖率用例不回归）
- [x] 1.4 既有 `tests/nodes/test_debate*.py`、`tests/evals/test_extract.py` 全绿

## 2. 锚点校验与 state channel（TDD 先红后绿）

- [x] 2.1 红灯：`check_argument_anchors`——data 型 resolved/unresolved（复用 `_resolve_field_ref`，含负索引与 DataFrame 行键.列名用例）/ event 型回声命中与未命中（复用回声源集合与 `_norm_text`）/ inference 有锚与零锚 / data、event 零锚 `missing` / unspecified 不校验 / 任一 resolved 即 `anchored`
- [x] 2.2 实现纯函数（零 LLM），辩手与三方风控节点解析后调用，结果写 `debate_anchor_checks` + `update_current_span(metadata=...)`
- [x] 2.3 `AnalysisState` 声明 `debate_anchor_checks: Annotated[list[dict], add]`；补进 `tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared` 断言集合（红灯先证明未声明会被丢弃）
- [x] 2.4 fail-open 回归：全部论点 unanchored 时路由与变更前一致（既有路由用例 + 新增断言）

## 3. 辩手 prompt 锚点申报纪律

- [x] 3.1 红灯：提示词契约测试（沿「提示词契约可测试性」）——三份模板 `key_arguments` 示例为结构化项 / 含「data 型必须附 field_ref 且只能引用输入数据段英文键」/ 含「禁止为推断伪造 field_ref / 拿不准标 inference」
- [x] 3.2 修改 `prompts/bull_debater.md` / `bear_debater.md` / `risk_debater.md`（输出契约 + 纪律段 + 一组 data/event/inference 各一条的示例）
- [x] 3.3（2026-09-16 完成）三份辩手 prompt 已发布 Langfuse **v23**（remote==local 回验、预检 PASS、`_verify_prompt_sync=[]`）。**偏离记录**：预检因「先提交后发布」顺序误判方向（远程 v22==历史提交 4c4b1b8，属旧部署版本），`sync_prompts --dry-run` 会反向覆盖本地（已避免）→ 以 git 历史证明方向后按 deploy 同逻辑 SDK 直推；治理缺口已登记于验证报告 §4

## 4. 评估器 / judge 材料 / 消融记录

- [x] 4.1 红灯：`tests/evals/test_evaluators.py`——`argument_anchor_coverage` value 与四个拆项 / 空 checks 返回 null / 不调 LLM
- [x] 4.2 `evals/evaluators.py` 实现；`evals/run.py` 上报 NUMERIC Score 并入实验报告
- [x] 4.3 红灯 + 实现：`evals/extract.py` debate_quality 材料加「【锚点覆盖】」骨架行与每条论点 `[kind 状态]` 前缀（状态取自 channel，不重新推断）；rubric 版本与 `points` 契约断言不变
- [x] 4.4（2026-09-16 解阻后完成，commits `0132f1e`+`a31793d`，审查 Spec ✅/Approved）消融 run 记录携 `argument_anchor_coverage` value+拆项；`aggregate_results` 出层增量 `{diff_mean, ci, conclusion}`（配对单元=标的，judge 块 golden guard 不变）；驱动 run 记录同步；零 LLM 44 passed
- [x] 4.5 `tests/evals/` 全绿

## 5. 真实链路验证（人工环节，不可跳过——incident 027 教训）

- [x] 5.1（2026-09-16）两次 deep（600519）完成：state channel 条目数=论点总数（49/45）✅；trace 根 span metadata 含 `anchor_stats` ✅（**边界如实记录**：共享根 span 下最后写入者胜，逐辩手归属以 state channel 为准）；Score `argument_anchor_coverage=0.2222` 落真实 trace ✅（evaluator 离线执行于本轮输出后按 trace id 落回）
- [x] 5.2（2026-09-16）遵从率分布：覆盖 16.3%/22.2%；`unspecified` **0**、`missing_required` **0–1**、`unresolved` 51%/44%、`unanchored_inference` 33%/31%。unresolved **逐条核对全为路径格式错误、无编造**（自造 `fundamental./macro./technical.` 前缀 / 描述混入锚点 / event 整句改写；风控侧照抄示例键 100% 成功）。token 增量代理：+76 字符/论点（结构化 vs 纯文本）
- [x] 5.3 报告落 `tests/validation/2026-09-16-add-debate-argument-anchors-validation.md`；预登记判定命中（unresolved 高）→ **prompt 迭代候选登记**（词表引导+坏例对照+event 短引语），未放松校验/未改判 kind

## 6. 收口

- [x] 6.1（2026-09-16）metrics.md：§1.2 指标行 + 时间线「judge 材料口径切点（2026-09-16）」+ §3 候选 (c) 标已落地（rubric 纳入留待校准轮）
- [x] 6.2 `openspec validate add-debate-argument-anchors --strict`（valid）；相关测试区 549 过（anchor/debate 契约 + causal_ablation）+ 2026-09-19 全套回归（2991+1049 过）+ ruff/mypy 改动文件净
- [x] 6.3 归档前置：本文件全勾 + 5.3 验证报告落盘（2026-09-16）+ spec sync（归档时合入）
