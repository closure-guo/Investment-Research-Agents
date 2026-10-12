# Tasks: add-analyst-factor-tools

## 1. 只读工具
- [ ] 1.1 实现 `query_factor_library`（按标的返回 active 因子取值/z-score/近60日IC）+ 输出 schema 合同单测
- [ ] 1.2 工具只读性保证（模块无写路径）+ 静态检查或单测

## 2. 注册与追踪
- [ ] 2.1 `agent_factory.py` 注册至技术分析师工具集 + Langfuse 用途标签 + wiring 单测

## 3. 消融变体
- [ ] 3.1 `evals/ablation.py` 新增「±因子工具」变体 + 配对 bootstrap 跑批

## 4. 验证
- [ ] 4.1 全量测试通过
- [ ] 4.2 n=10 消融报告落 `docs/evals/`，结论按「CI 含 0 只写无显著差异」口径撰写
- [ ] 4.3 既有测试无修改通过（红线）
