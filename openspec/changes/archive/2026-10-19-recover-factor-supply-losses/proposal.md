# 回收因子供给线可恢复损耗（recover-factor-supply-losses）

## Why

供给线复盘（2026-10-19，收割状态文件 80 条已处理候选逐条归因）显示：**40/80 条候选"没考成"，
其中一半是仓库内可修的机械损耗**，不是因子质量问题——

| 损耗族 | 条数 | 根因（代码级） | 可修性 |
|---|---|---|---|
| 负数常量 | 6 | `qlib_translate._build` 对 `_NegNumber` 保守拒绝（detail 自述「可用 sub 重排表达，翻译器 v1 保守拒绝」）——DSL 本身可严格等价表达 | **本 change 修** |
| 深度超限（仅深度轴） | 3 | `MAX_AST_DEPTH=6`；illiq20/illiq_20d 深度 7、klow_5d 深度 8——上限 6 的注释「7+ 当前无解锁对象」已过时 | **本 change 修**（有 4→5→6 两次同款先例） |
| 转译响应合同失败 | 2 | LLM 返回非 JSON → `transcription_error` 一次性落账 | **本 change 修**（重试） |
| 修复无法生效 | 42 | 收割桥一次性判定：状态落账后永不自动重试（含 untranslatable/transcription_error/rejected） | **本 change 修**（按状态重放通道） |
| LLM 拒译（缺算子） | 21 | 需 sign / 滚动 Min-Max / 比较指示等算子 + prompt 清单同步（Langfuse 部署） | 独立决策（本 change 明确不做） |
| 研究侧产物质量 | 8 | 公式含未数值化 ε（7）/ 公式残缺（1）——应治研究侧 | 本 change 明确不做 |
| 横截面/PCA/回归 | 4 | 换计算模型级别 | 本 change 明确不做 |

合计本 change 可让 **约 11 条候选**（6 负数常量 + 3 深度 + 2 重试）重新进入五道闸，
并让「已考 38 条全拒」具备复检能力（面板 150→250 后闸①读数结构性改善，此前拒收结论
SHALL 可被重放复检）。

## What Changes

- **MODIFIED「外部表达式翻译契约」（factor-proposal）**：负数常量以严格等价的减法重排表达
  （`-1 * x` → `sub(0,x)`；`x - (-k)` → `add(x,k)` 等，逐条列于 design.md），不再构成
  不可翻译边界；重排产物经同一复杂度硬约束复验。
- **MODIFIED「复杂度硬约束」（factor-dsl）**：AST 深度上限 6 → 8（自由参数 ≤3 与字段种类 ≤3
  两条**不动**；证据 = 12 条复杂度拒收中仅深度轴 3 条，其自由参数均在限内）。
- **MODIFIED「外部研发供给接入」（factor-proposal）**：
  - 转译响应不合载荷合同（非法 JSON / 缺字段）SHALL 重试，上限 2 次（共 3 次尝试，与
    「提案输出合同」修复重试同款上限）；重试耗尽仍失败 → `transcription_error` 留痕
    （含尝试次数）；**网关级异常不重试**——维持 fail loud 上抛语义不变。
  - 收割通道 SHALL 支持**按状态重放**（`retry_statuses` 参数 + ops 脚本入口）：已处理候选
    命中指定状态时重新进入转译/翻译/过闸链路；新裁决覆盖旧记录并保留 `retried_from`
    供审计；未指定时逐字维持现幂等语义（重复运行 MUST NOT 重复导入）。
- **实现面（非规范变更）**：`factors/qlib_translate.py`（重排）、`factors/dsl.py`（常量 + 注释）、
  `factors/harvest.py`（重试 + 重放）、`scripts/replay_factor_harvest.py`（ops 入口）、
  `prompts/factor_proposal.md`（「AST 深度 ≤ 6」→「≤ 8」，按 prompt-deploy-consistency 发布）。

## Capabilities

- **Modified Capabilities**:
  - `factor-proposal`（外部研发供给接入 / 外部表达式翻译契约）
  - `factor-dsl`（复杂度硬约束）

## Impact

- 代码：`src/finance_agent/factors/{qlib_translate,dsl,harvest}.py`；
  `scripts/replay_factor_harvest.py`（新增）；`src/finance_agent/prompts/factor_proposal.md`
- 测试：`tests/factors/test_qlib_translate.py`（负数常量重排矩阵 + 深度 8 用例）、
  `tests/factors/test_dsl.py`（深度边界翻转）、`tests/factors/test_harvest.py`（重试 / 重放）
- **不做（明确边界）**：
  - sign / 滚动 Min-Max / 比较指示算子（需 prompt 清单同步 + Langfuse 部署；独立决策，
    本 change 的测量读数为其提供依据）；
  - 自由参数上限（3）不动——多参数拟合是无全局账本前最需防的过拟合轴（闸②门槛复审立场）；
  - 研究侧产物质量（ε 数值化）治研究侧，不在本 change；
  - 横截面 / PCA / 回归计算模型扩展不在本 change；
  - 闸② 门槛（0.3）不动（另一条独立决策线）。
- 复跑证据：重放测量（恢复候选过闸读数 + 已拒 38 条复检）→ `tests/validation/` 验证报告。
