# Design: add-fm-grounding-surface

## 上下文

issue #242 断点 3。既有先例：`update-decision-integrity-gates` 的「FM 审批对象完整性可见性」——终稿完整性标注进 FM 上下文，**仲裁权保留（标注不禁止 approve）**。本 delta 沿同一模式扩展三类 grounding 输入。

## 决策

### D1 仲裁权保留，不做自动退回（与 issue 处置建议「退回重评」的差异）

issue 建议「估值缺席或口径冲突命中时退回重评」。不改路由自动退回的理由：

1. 既有 spec 契约明确「FM MUST NOT 因上下文含标注而被禁止 approve（仲裁权保留，可见性义务优先）」——同一审批面上的新标注遵循同一原则，避免同面两套语义。
2. 自动退回的触发条件（「估值缺席」的定义：missing_reasons 非空即退？）无观测数据支撑阈值；FM prompt 已指导 return 的适用语义（「存在可修正的缺陷」），估值缺席是否致命由 FM 结合方案内容判断（例如纯技术面方案可不受估值缺席影响）。
3. 先归因后处置（incident 026 纪律）：本 delta 落「可见性 + 回应义务」，观测 FM 在标注出现下的行为分布后再评估是否升级为硬门禁。

prompt 中以「MUST 回应」替代「禁止 approve」：出现标注/告警时 reasoning 必须显式处理，评估面可审计 FM 是否回应。

### D2 披露节原文整段注入，不做叙事一致性程序比对

「口径一致性（披露节 vs 叙事）」的程序化比对需要解析 FM/终稿叙事文本（LLM 输出），必然引入解析脆弱性（参考报告导出解析病史）。披露节本身是确定性渲染，整段进 FM 上下文，由 FM（LLM）在审批时交叉核对——一致性判断本就是审批语义层的职责。程序只负责让 FM「看得到」。

### D3 锚点告警的聚合口径

告警面取三类信号（全部来自 `debate_anchor_checks`，零重算）：

- `value_mismatch`：status=value_mismatch 的论点（文本数字不可溯源——最强信号）
- `echo_only_field_refs`：field 形态锚仅回声命中（声明与证据来源错位）
- unresolved 计数（`data`/`event` 型 missing 或 status=unresolved）：含光大 mode C（field 形态锚未解析）

逐条列示上限 5 条（role/round/index/anchors/status），超出计数汇总——防上下文体积失控（analyst-context-budget 同思路）。全零不出段（上下文体积纪律：非空才出现）。

### D4 prompt 变更用条件式条款

「上下文出现 X 段时」的条件式表述——代码未部署时（prompt 先发 Langfuse）上下文不含新段，条款自然失活，不产生悬空指令。合并后执行 `uv run python scripts/deploy_prompts.py`（prompt-deploy-consistency 门禁）。

### D5 消费面与产出面的时序

本 delta 堆叠在 `feat/anchor-value-grounding`（断点 2）之上——告警聚合读 `matched_via`/`echo_only_field_refs` 字段，依赖断点 2 的记录契约。#307 合并后本 PR retarget main。

## 风险

- 上下文体积：三段均为非空才出现；披露节现网实测 ~5-8 行；告警面封顶 5 条。风险可控。
- FM 行为漂移：新段可能改变 approve/reject 分布——评估面已有 confidence 漂移/职责边界审计，标注回应义务使变化可解释。
