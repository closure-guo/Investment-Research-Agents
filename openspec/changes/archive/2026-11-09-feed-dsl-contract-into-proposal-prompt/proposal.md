# Proposal: feed-dsl-contract-into-proposal-prompt

## Why

#482（feed-dsl-contract-to-factor-generator）把 DSL 契约送进了**跨会话记忆块**——即首轮 prompt 里位于已探索清单之前的上下文段，受 9000 字符预算管辖、可被截断，且依赖反馈文件存在。生成 prompt 的**模板本体**（RD-Agent 场景的提案任务指令层）仍不含任何 DSL 约束：约束以「上下文」而非「任务定义」的权重传达，模型可以绕过。这是 #482 落地时明确留下的 ROI ①（剩余优化选项之首）。实施以 #482 判效读数为前置（10-31 归档）：若契约进上下文已把 untranslatable 打到目标区间则本 delta 降级；若效果不足（当前首日读数 88.7%→73.7%、仍 0 新入库）则本 delta 是下一变量——评估纪律一次只动一个变量。

## What Changes

- 生成侧提案 prompt 模板本体（distro 内 `rdagent/scenarios/qlib/proposal` 的 prompt 模板）内嵌 DSL 表达契约段：复杂度上限 + 算子/字段白名单 + 三条表达规则。
- 契约段内容**由单一真相源派生**（`factors.dsl` 复杂度常量、`factors.qlib_translate` 映射，经记忆桥导出产物的 `contract` 块消费），MUST NOT 在生成侧手工复制字面量形成第二真相源；DSL 表达力扩展（如未来横截面算子）时契约段随真相源自动更新。
- prompt 契约段与记忆桥载荷 `contract` 块 SHALL 数值一致（同一导出派生）；契约源不可用时 prompt 组装 SHALL fail-safe（不阻断生成，降级为仅记忆桥契约或无契约段）。
- 分发机制沿用既有 distro 补丁管线（`tests/scripts/spike/patch-explored-feedback.py` 同款路径），不 fork distro 仓库。

## Capabilities

- **New Capabilities**: 无（capability `factor-proposal` 下 ADDED 一条新需求，与 #482 的「生成侧 DSL 契约回喂」（记忆桥路径）为不同 requirement、不同传达层）

## Impact

- `tests/scripts/spike/`（补丁脚本族：prompt 模板补丁 + 一致性校验工具）
- `src/finance_agent/factors/feedback.py` 零改动（契约导出已存在）
- distro side-packages（部署面，同 #482 的补丁部署管线与 `/app/scripts` 挂载坑约束）
- 判效依赖：实施前必须先归档 10-31 前后 #482 的 untranslatable/入库读数，作为本 delta 的对照基线
