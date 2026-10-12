# Tasks: add-factor-proposal-agent

## 1. 提案节点与合同
- [ ] 1.1 实现 `factors/propose.py` + 输出合同（expression/hypothesis/category 必填校验）+ stub LLM 单测
- [ ] 1.2 DSL 解析失败走 repair 重试（上限 2 次）后丢弃并记 trace + 重试上限单测

## 2. 双模型与采样
- [ ] 2.1 PolicyRouter 注册 `factor_proposal` 用途（quick 广度/deep 改进）+ 路由单测
- [ ] 2.2 分桶统计与算子先验表注入 prompt + 跨桶采样断言单测

## 3. 对齐软评分
- [ ] 3.1 假设-因子一致性评分写入元数据（软信号，不拒收）+ 元数据落库单测

## 4. 验证
- [ ] 4.1 全量测试通过
- [ ] 4.2 50 条提案人工核对（可解析率/假设完整率），报告落 `tests/validation/`
- [ ] 4.3 既有测试无修改通过（红线）
