# Tasks: add-outcome-profitability-protocol

## 1. 口径登记（先于一切代码）

- [x] 1.1 `docs/evals/metrics.md` §1 新增 §1.8「Outcome 收益指标」：主指标（T+20 超额 vs 000300，均值+胜率）/ 胜率沿用 ±2% 中性带口径 / 人口划分（可执行主结论、neutral 回避辅助）/ T+5/T+10 辅助观测窗 / settled<10 红线 / 泄漏控制声明
- [x] 1.2 `metrics.md` §2 时间线追加「outcome 口径启用」切点行（注明：Δ2 落地前读数条款为已登记未启用）
- [x] 1.3 `runs.jsonl` outcome run 类型约定（`type: outcome-forward | outcome-backtest`）写入 §1.8 口径行说明

## 2. 预登记

- [x] 2.1 首个 outcome 预登记文档落 `evals/ablation/preregister/`（门禁字段全：主指标 / MDE 反算过程 / 决策阈值 / 样本量依据（forward ≥10 红线、≥30 完整结论；回测按 MDE 反算）/ 停止规则 / 成本分型 / 泄漏控制）
- [x] 2.2 `preregister.py` 门禁字段解析兼容性验证（新文档可被解析、缺字段报错）+ 单测

## 3. 收口纪律工具化

- [x] 3.1 outcome 健康检查脚本：结算成功率 / 行情缺失率 / 污染护栏（测试库隔离 + integrity_check 通过）/ 记账完整率，输出机器可读结果
- [x] 3.2 两句式结论校验：复用/扩展 `causal_ablation/conclusion.py` 机制——显著句式须带 CI、分辨率不足句式须带 MDE、裸「未获统计支持」判非法 + 单测
- [x] 3.3 收口报告生命周期 status 头接线（复用 `causal_ablation/report_status.py` 校验与 `docs/evals/README.md` 索引渲染）+ 单测

## 4. 验证与收口

- [x] 4.1 全量测试绿（`uv run pytest`）+ `uv run ruff check` + `uv run mypy`（触碰文件零新增错误）
- [x] 4.2 人工验证报告落 `tests/validation/`（口径条目逐条对照 spec Scenario 核对）
- [ ] 4.3 `openspec validate add-outcome-profitability-protocol --strict` 通过；tasks 全勾后按 §3 Step 6 sync + archive —— **validate 已过（2026-09-23，EXIT=0）；sync/archive 待读数腿落地后统一（口径先行，不随本 delta 单独 archive）**

## 依赖与顺序说明

- 本 delta 无生产代码变更，可先行合入；读数腿开跑前置 = 本 delta + `update-decision-settlement-contract` 均已落地
- Owner 决策点（合入前须裁决）：① 主窗口 T+20；② 泄漏探针降级阈值（Δ4 建议 60%）；③ 两腿预算（Δ3/Δ4）
