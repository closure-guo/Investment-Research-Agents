# Tasks: add-paper-broker

## 1. Broker 抽象与 paper 实现
- [ ] 1.1 base.py 抽象接口 + paper.py（撮合走 matching-engine）+ 全链路单测
- [ ] 1.2 持仓/资金 SQLite 落库 + 幂等结算单测

## 2. 战绩对照
- [ ] 2.1 仪表盘虚拟 vs 仿真双曲线 + 执行损耗标注 + 前端 Vitest
- [ ] 2.2 README 免责声明增补

## 3. 验证
- [ ] 3.1 全量测试通过
- [ ] 3.2 一个月回放 10 笔成交人工核对，报告落 `tests/validation/`
- [ ] 3.3 既有测试无修改通过（红线）
