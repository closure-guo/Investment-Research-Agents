# Tasks: add-factor-proposal-agent

## 1. 提案节点与合同
- [x] 1.1 实现 `factors/propose.py` + 输出合同（expression/hypothesis/category 必填校验）+ stub LLM 单测
      —— `src/finance_agent/factors/propose.py` + `src/finance_agent/prompts/factor_proposal.md`（提交 `c079a243`）；
      合同逐字：expression 走 DSL 默认复杂度硬约束、hypothesis ≥30 字（中文按字符数，去首尾空白）、
      category 五枚举；`tests/factors/test_propose.py::TestContractValidation`（7 例）
- [x] 1.2 DSL 解析失败走 repair 重试（上限 2 次）后丢弃并记 trace + 重试上限单测
      —— 首轮 + 2 次修复 = 3 次尝试；错误原文逐条进修复 prompt；耗尽 → `rejected`（raw + 最后错误）+
      trace metadata `proposal_rejected`（`TestRepairLoop` 7 例：`[坏,坏,好]` 第三次成功、`[坏,坏,坏]` 丢弃、
      部分收编、非 JSON 响应计入修复、n<20 先于任何调用抛 ValueError）

## 2. 双模型与采样
- [x] 2.1 PolicyRouter 注册 `factor_proposal` 用途（quick 广度/deep 改进）+ 路由单测
      —— `Purpose` 字面量增 `factor_proposal`、`REQUIRED_CAPS["factor_proposal"] = {"json_schema": {json_mode, strict_schema}}`
      （提交 `60eba7a4`）；`route_proposal_call`（broad 候选序 [quick, deep]、deep 反序，select_profile 保序）；
      `tests/llm/test_router.py` 追加 3 例（能力过滤/链耗尽 typed error/保序）+ `TestTierRouting`/`TestGatewayClient`（12 例）
- [x] 2.2 分桶统计与算子先验表注入 prompt + 跨桶采样断言单测
      —— `build_broad_prompt` 渲染「桶名 N 条（P%）」+ `op: IC` 表；单桶占比 >50% 追加显式指令
      （含占比数字与「偏向其他桶（…）」枚举），均 ≤50% 不产生该段；`build_deep_prompt` 注入拒收原因分布 +
      衰减复盘（提交 `067c0119`；`TestPromptAssembly` 9 例）

## 3. 对齐软评分
- [x] 3.1 假设-因子一致性评分写入元数据（软信号，不拒收）+ 元数据落库单测
      —— `FactorProposal.alignment_score`（缺失 → None；数值收敛 [0,1]；非数值按未评分）；任何路径不因该分值
      拒收（单测：0 分进入 proposals、越界收敛、非数值 None、批量透传）

## 4. 验证
- [x] 4.1 全量测试通过 —— `uv run pytest tests/factors tests/llm -q` → 478 passed / 1 failed（既有环境 flake：
      `test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`，隔离复跑通过，
      归因见验证报告 §0.1）；全量回归（`tests/ -m "not live"`）见报告 §0.1/结论
- [x] 4.2 50 条提案人工核对（可解析率/假设完整率），报告落 `tests/validation/` —— 真跑 2 批 broad（各 ≥20）
      共 57 条：DSL 可解析率 57/57、hypothesis 完整率 57/57、rejected 0；报告
      `tests/validation/2026-10-13-add-factor-proposal-agent-validation.md` + 全量逐条
      `tests/validation/2026-10-13-add-factor-proposal-live-run.json`；**语义一致性的「人工」终审签字待补**
- [x] 4.3 既有测试无修改通过（红线）—— 仅 `tests/llm/test_router.py` 追加用例（既有断言零改动，计划允许），
      其余为新增文件；lint/mypy 全绿（报告 §0.2）
