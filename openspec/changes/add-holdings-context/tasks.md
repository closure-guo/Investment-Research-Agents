# Tasks: add-holdings-context

- [ ] `HoldingsContext` 模型（三态）+ `state["holdings_context"]` 声明 + `AnalyzeRequest.holdings` 可选声明 → `initial_state`（含未声明/未持有不互相归一的单测）
- [ ] 决策层受众持仓行注入：trader / risk_judge context 三态各自注入（含「未申报不省略该行」的单测）
- [ ] `trader.md` / `risk_judge.md` 受众约束段（A 股普通账户不可做空 + 持仓三态方向表达纪律 + 结论可执行性）+ 提示词契约测试
- [ ] 未申报 sell_type 默认 short → exit（`risk.py` 两处：打回后默认 + 滞回维持前判路径）+ 单测
- [ ] 价位完整性打回措辞按分型分支（缺 `exit_schedule` → 要求减仓节奏；缺建仓价位 → 现行措辞）+ 单测
- [ ] 未持有受众的 exit 可执行性门禁（`holds=False` + sell/exit → 打回重申一次 → 仍犯降级 watch + 如实标注落 state）+ 单测
- [ ] 报告做空披露行（`sell_type=short` 与历史 None 形态；exit/buy/watch/hold 不渲染）+ 单测
- [ ] `scripts/deploy_prompts.py` 发布 trader / risk_judge 提示词（Langfuse 指纹一致性通过）
- [ ] 全量验证：`uv run pytest` / `uv run ruff check` / `uv run mypy` 通过
- [ ] 人工验证报告落 `tests/validation/`（抽查真实报告「交易决策」节披露行与受众约束生效）
