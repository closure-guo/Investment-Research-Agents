# Design: add-watch-trigger-tracking

## Approach

四处消费同一个结构化字段，每处都复用既有先例，不发明新机制：

1. **契约层**：`TradeDecision` 加 `trigger_high`/`trigger_low: float | None`，清洗走宽松先例（非数值/负值/NaN → None，不抛异常）。必填约束不加在 schema 层，由终稿完整性检查承担——与 `require-watch-hold-rationale`（inaction_reason/reeval_triggers）完全同型：打回一次，仍缺放行 + 如实标注。
2. **校验层**：`check_decision_prices` 已持有完整 `TradeDecision` 与 `latest_close`，在其文本校验旁加结构化分支即可——`empty_trigger` 语义（上破价须高于现价/下破价须低于现价）已存在，从「解析文本再判」变为「直接消费字段」，反而更可靠。anomaly 走既有门禁分层，与文本 anomaly 共用一次打回预算。
3. **呈现层**：K 线 `_DECISION_LEVEL_KEYS` 同型扩展两个键；报告渲染只在 watch 模板加两行 + 入池声明行 + 真空提示行（间隔阈值做成配置项，默认 3 自然日）。
4. **数据层**：`predictions` 表 ALTER 加三列；`persist_prediction_from_accumulated` 已持有 `session_id` 参数与终稿决策，补齐写入即可。存量行 NULL 不追溯。

**顺序约束**：报告生成（report 节点）早于入池（api/agent_factory 在管线完成后调 ingest）。因此报告内只渲染静态入池声明（不写死 prediction_id）；报告 ↔ 池记录的关联靠 `predictions.session_id` 列由查询侧反查（前端会话页展示属后续交互类 delta）。

## Alternatives Considered

- **解析 `reeval_triggers` 文本提取价位入图/入库**：被否。价位校验器七发修复史（#255/#257/#258/#259）已证明中文行文解析是脆弱路径；让 LLM 直接申报结构化字段，校验器只做方向核对，把脆弱性消灭在源头。
- **把入池挪进流水线（report 节点前）让报告嵌入 prediction_id**：被否。动管线顺序影响面广（阻断分支、重试路径都要重排），收益仅省一次反查；`session_id` 列已支撑查询侧关联。
- **触发位做成 list 结构支持多档**（trigger_high_1/2…）：被否。双向各一档覆盖 watch 语义；多档留给文本 `reeval_triggers` 承载。

## Risks

- **LLM 申报率低**：触发位是新增申报义务，初期可能大量「已打回仍未申报」。对策：trader prompt 写明申报纪律与方向语义（上破 > 现价 / 下破 < 现价），打回 feedback 引用风险辩论价位线索；申报率进观测（标注计数可查 Langfuse）。
- **结构化与文本不一致**（字段 24.6 vs 文本「站上 24.8」）：接受——结构化字段为机器消费真源，文本为人类语义；门禁只对两者各自的方向/偏差校验，不做交叉一致性校验（留给观测发现）。
- **DDL 迁移撞运行中日批**：predictions 加列须在无 running 会话、日批窗口外执行（AGENTS.md 红线同型纪律）。
