# Tasks: add-holdings-context

- [x] `HoldingsContext` 模型（三态）+ `state["holdings_context"]` 声明 + `AnalyzeRequest.holdings` 可选声明 → `initial_state`（含未声明/未持有不互相归一的单测）——含请求接入三态参数化测试（变异验证可红）与 ReAct 追问路径接线
- [x] 决策层受众持仓行注入：trader / risk_judge context 三态各自注入（含「未申报不省略该行」的单测）——两处注入点均三态表驱动
- [x] `trader.md` / `risk_judge.md` 受众约束段（A 股普通账户不可做空 + 持仓三态方向表达纪律 + 结论可执行性）+ 提示词契约测试
- [x] 未申报 sell_type 默认 short → exit（`risk.py` 两处：打回后默认 + 滞回维持前判路径）+ 单测——另含全重试后终稿收口（sell+None → exit 并如实改注）
- [x] 价位完整性打回措辞按分型分支（缺 `exit_schedule` → 要求减仓节奏；缺建仓价位 → 现行措辞）+ 单测
- [x] 未持有受众的 exit 可执行性门禁（`holds=False` + sell/exit → 打回重申一次 → 仍犯降级 watch + 如实标注落 state）+ 单测——含「门禁判据终稿复核」（零 LLM 确定性降级，覆盖收口兜底与后续打回翻回路径）
- [x] 报告做空披露行（`sell_type=short` 与历史 None 形态；exit/buy/watch/hold 不渲染）+ 单测
- [x] `scripts/deploy_prompts.py` 发布 trader / risk_judge 提示词（Langfuse 指纹一致性通过；回验 production 含周期契约段且无「默认按 short」残留；`_verify_prompt_sync` 空列表）
- [x] 全量验证：`uv run pytest`（4645 passed / 4 failed，与干净 `origin/main` 基线失败集完全相同 → 零回归、新增 46 用例）/ `uv run ruff check`（passed）/ `uv run mypy`（83 errors = 基线 83，零新增）
- [x] 验证报告落 `tests/validation/2026-10-12-add-holdings-context-validation.md`（自动化证据 + 真实 LLM 三态冒烟）；**owner 签字待补**
