# Tasks: update-decision-buy-gates

- [x] 周期契约段改为纯结算口径声明（contracts.py HORIZON_CONTRACT + trader/risk_judge/fund_manager 三 md 同步，契约测试拦截漂移）
- [x] trader 增加止损锚定与仓位预算规则（max(2×日波动, 结构位) + 禁止带内自由选边 + 仓位预算化）
- [x] risk_judge 否决纪律与参数协商（致命白名单 + 联合修正/参数缺口二选一 + 废除「证据均衡倾向 hold/watch」）
- [x] 证据均衡带收窄：增量申报义务仅约束方向翻转（execution/hysteresis.py + nodes 侧检查 + risk_judge prompt 同步）
- [x] 4 分析师 horizon_hint 括注去除决策判语（保留申报义务）
- [x] confidence 中档示例去锚定（三 prompt 区间化 + 反锚定纪律）
- [x] tests/test_prompt_contracts.py 与滞回测试更新至新契约（先红后绿）
- [x] 合成回归四案例通过：v4 冷却必 buy、v5 窄影线必 buy（或 buy+显式参数缺口）、defect 必 sell、v3.1 不得引用催化剂/均衡/止损不可用类理由
- [x] scripts/deploy_prompts.py 发布 Langfuse production（回滚路径 = 重发上一版本）
- [x] 生产库完整性核验（predictions/sessions 计数不变）+ 实验报告归档
