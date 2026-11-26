# Tasks: update-veto-discipline-negative-ev

- [ ] risk_judge 提示词权威源（`src/finance_agent/prompts/risk_judge.md`）新增负期望否决项与边界段落；trader.md 不动
- [ ] prompt 契约测试：模板含负期望条款与「赔率不足但期望非负不得否决」边界表述（先红后绿）
- [ ] `uv run python scripts/deploy_prompts.py` 发布新版本（prompt-deploy-consistency 门禁）
- [ ] 历史决策离线回放抽验：000957 类（负期望）应改判 watch 且带数值依据；600938/920136 类（赔率<2 但期望非负）应保持 buy；11-20 审计样本方向层结论不得批量翻转
- [ ] 独立评估批（一次只动一个变量）：批号/时间线与均值记入 `docs/evals/metrics.md` 时间线 + `runs.jsonl`
- [ ] 人工抽查 ≥10 条新批决策，确认无「期望为负却仍维持 buy」且无「普遍 watch 回摆」；报告落 `tests/validation/`
- [ ] 非交互类变更（无 UI/SSE/状态流转）→ E2E 门禁不适用，任务表注记
