# Tasks: raise-factor-dsl-depth-cap

> 上线顺序 = 任务顺序。TDD：先红后绿。

## T1 DSL 常量与消息（factors/dsl.py）

- [x] 先红后绿：`MAX_AST_DEPTH == 5`；深度恰 5 通过 / 深度 6 拒收且消息含「深度 6 > 上限 5」
- [x] 三条上限并列违规用例加深一层（深度 6 触发深度违规，消息逐字）
- [x] RecursionError 转译消息随新上限（「> 上限 5 层」）；文档串同步

## T2 翻译器用例（tests/scripts/spike/test_qlib_translate.py）

- [x] 复杂度拒收用例改为深度 6（5 层嵌套 Mean）；新增「深度 5 现可翻译」用例
      （`Mean($open / Ref($close, 1) - 1, 5)` → `ts_mean(sub(div(open,ref(close,1)),1),5)`）

## T3 prompt 同步 + 发布

- [x] `factor_proposal.md`「AST 深度 ≤ 4」→「≤ 5」（只改数字，算子清单不动）
- [x] 发布（Langfuse production 在产校验通过）；预检拦截 risk_judge/trader（他处内容未收编）
      → 以 `--exclude` 定向发布，不触碰非本 delta prompt

## T4 复跑与证据（第三轮）

- [x] 批 A（10 条）重跑：**10/10 可翻译**（预测命中）+ 过闸（闸②×6、闸④×2、闸①×2）
- [x] 批 B（22 条）重跑：**15/22 可翻译**（预测命中：深度 5 的 4 条解锁；clv_5d 深度 6、
      amihud×2 深度 8+参数 4 仍拒）+ 过闸（闸②×14、闸①×1）
- [x] 读数落档 → 验证报告 `tests/validation/2026-10-17-raise-factor-dsl-depth-cap-validation.md`
      （含闸①×窗口长度交互的量化取证：20 日窗贴线 20.117%）

## 收口

- [x] `uv run pytest tests/factors tests/screening tests/test_api_factors.py tests/test_api_screening.py
      tests/scripts/spike/test_qlib_translate.py -q` 全绿（522）；ruff 干净
- [x] `openspec validate raise-factor-dsl-depth-cap --strict` 通过
- [x] 验证报告 + PR；sync + archive 在验证通过后执行
