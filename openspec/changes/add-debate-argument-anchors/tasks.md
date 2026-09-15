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
- [ ] 3.3（**门控：Langfuse/Docker 停机，发布未执行**——prompt 已改并过契约测试，部署动作与 eval 门禁解锁同在 Task 6） `uv run python scripts/deploy_prompts.py` 发布，记录发布输出（否则 eval 门禁拒跑）

## 4. 评估器 / judge 材料 / 消融记录

- [x] 4.1 红灯：`tests/evals/test_evaluators.py`——`argument_anchor_coverage` value 与四个拆项 / 空 checks 返回 null / 不调 LLM
- [x] 4.2 `evals/evaluators.py` 实现；`evals/run.py` 上报 NUMERIC Score 并入实验报告
- [x] 4.3 红灯 + 实现：`evals/extract.py` debate_quality 材料加「【锚点覆盖】」骨架行与每条论点 `[kind 状态]` 前缀（状态取自 channel，不重新推断）；rubric 版本与 `points` 契约断言不变
- [ ] 4.4（**门控：并发工作流 `update-ablation-driver-parity-and-report-status` 持有 `evals/ablation.py` / `tests/scripts/ablation_pilot.py` / `tests/evals/test_ablation.py` 未提交改动**，消融接线延后为其落地后的独立小任务） 消融：`evals/ablation.py` run 记录 + `aggregate_results` 对该指标出配对 bootstrap CI；`tests/scripts/ablation_pilot.py` run 记录携带（零 LLM 用例）
- [x] 4.5 `tests/evals/` 全绿

## 5. 真实链路验证（人工环节，不可跳过——incident 027 教训）

- [ ] 5.1 本机跑一次 deep 全流程（真 LLM），Langfuse trace 核对：每个辩手 span metadata 含锚点统计；state `debate_anchor_checks` 条目数 = 论点总数；Score `argument_anchor_coverage` 落库
- [ ] 5.2 统计首轮遵从率分布：`unspecified` / data 型 `missing_required` / `unresolved` / `unanchored_inference` 各占比，逐条人工核对 unresolved 锚点是路径写错还是编造；token 增量（辩手 context/输出）实测
- [ ] 5.3 报告落 `tests/validation/YYYY-MM-DD-add-debate-argument-anchors-validation.md`；若 data 型无锚或不可解析比例高，处置对象只能是 prompt（登记迭代项），不得放松校验或改判

## 6. 收口

- [ ] 6.1 `docs/evals/metrics.md` §1.2 新增 `argument_anchor_coverage` 行；时间线标注 judge 材料切点；§3 遗留候选 (c) 更新为「已落地，rubric 纳入留待校准轮」
- [ ] 6.2 `openspec validate add-debate-argument-anchors --strict`；全量 `uv run pytest` 绿 + ruff/mypy（改动文件）
- [ ] 6.3 归档前置：本文件全勾 + 5.3 验证报告落盘 + spec sync
