# Tasks: add-screening-ranker

## 1. 打分纯函数
- [ ] 1.1 横截面 z-score + IC 加权合成 + 确定性单测（同输入同输出）
- [ ] 1.2 factor_contribs 贡献分解 + 勾稽单测（Σcontribution = composite_score，容差 1e-9）

## 2. builtin 因子包
- [ ] 2.1 五个手工因子的 DSL 表达与注册路径（免提案、过准入闸）+ 注册单测

## 3. 验证
- [ ] 3.1 全量测试通过
- [ ] 3.2 三个交易日 Top-100 抽 10 只手算核对，报告落 `tests/validation/`
- [ ] 3.3 既有测试无修改通过（红线）
