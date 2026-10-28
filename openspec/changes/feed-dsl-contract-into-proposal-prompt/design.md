# Design: feed-dsl-contract-into-proposal-prompt

## Approach

复用 #482 已验证的「数据驱动渲染 + distro 补丁管线」形态，把渲染点从记忆块追加扩展到 prompt 模板本体：

1. **契约源不变**：唯一真相源仍是 `factors/feedback.py::_contract_block`（import `dsl` 常量与 `qlib_translate` 映射）。模板契约段**读取同一导出产物**（`explored_factors.json` 的 `contract` 块）渲染，模板补丁脚本里零契约字面量——数值一致（Scenario 3）由「同源」结构性保证，而非靠校验对齐两份副本。
2. **补丁点**：distro 的 `QlibFactorHypothesisGen` prompt 装配处（与 `explored_feedback` 追加点同一类钩子），在任务指令模板开头注入契约段（规则行 + 复杂度 + 白名单摘要）。白名单全文可能超模板预算，压缩策略：规则三条全量、复杂度三个数值全量、白名单按「类别计数 + 高频算子样例」摘要——与记忆桥契约段（全量白名单）互补，不重复占预算。
3. **fail-safe**：补丁渲染函数契约源缺失/异常 → 返回空串跳过注入，生成照常（与 `explored_feedback` 同款语义）。
4. **分发**：沿用 `patch-explored-feedback.py` 补丁管线模式部署到 site-packages；补丁幂等（重复打不双份）。部署面注意 `/app/scripts` 只读挂载与镜像/挂载错配坑（#482 教训：容器内跑 CLI 用镜像内函数路径）。

## Alternatives Considered

- **改 distro 源码 prompt 模板文件**（fork/改文件）：distro 升级即丢、无法与真相源联动，形成手工第二真相源。否。
- **把白名单全量塞进模板本体**：模板预算挤占任务指令本身；全量白名单已在记忆桥契约段（首轮 prompt 上下文内），模板段做摘要即可。否。
- **等 #482 判效再立本 delta**：提案先行、实施后置——delta 即容器，前置判效已写进 tasks 第 1 项与 spec 实施前置，两不耽误。取。

## Risks

- **双变量归因混淆**（最大风险）→ 硬前置：10-31 读数归档前不实施；tasks 第 1 项不勾不得开工。
- distro 版本升级使补丁点失效 → 补丁带落点探针（装配处签名不匹配即 fail-safe 跳过并记日志），与 #482 补丁同款防御；升级后需人工复核一次。
- 契约段过长挤占生成 prompt 预算 → 白名单摘要策略 + 补丁内设段长上界；单测钉住段长。
- 模板契约与记忆桥契约未来漂移（一个改了另一个没跟上）→ 同源设计（同一 `contract` 块派生）+ 一致性测试（同输入断言两段数值一致）。
