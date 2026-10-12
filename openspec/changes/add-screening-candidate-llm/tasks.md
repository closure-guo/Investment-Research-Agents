# Tasks: add-screening-candidate-llm

## 1. 粗筛节点
- [ ] 1.1 实现 candidate_llm.py（quick profile + 输出合同 keep/drop+reason）+ stub 单测
- [ ] 1.2 并发执行与单只失败降级 keep + 并发与降级单测

## 2. 稳定性门槛
- [ ] 2.1 连续 3 日在榜门槛 + 边界单测（2 日不通过、3 日通过、中间断档重新计数）

## 3. 决策落库
- [ ] 3.1 keep/drop 决策落库（供消融与命中率分层）+ 落库单测

## 4. 验证
- [ ] 4.1 全量测试通过
- [ ] 4.2 一周候选 L3 抽 20 条 drop 理由人工核对，报告落 `tests/validation/`
- [ ] 4.3 既有测试无修改通过（红线）
