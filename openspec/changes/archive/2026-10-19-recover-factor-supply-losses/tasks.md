# Tasks: recover-factor-supply-losses

> 上线顺序 = 任务顺序。TDD：先红后绿。

## T1 负数常量严格等价重排（factors/qlib_translate.py）

- [x] 先红后绿：重排表逐条用例（`-1 * x` → `sub(0,x)`；`x - (-1)` → `add(x,1)`；
      `x * (-2)` → `sub(0,mul(x,2))`；`(-1) / x` → `sub(0,div(1,x))`；`(-2) - x` → `sub(0,add(2,x))`；
      负负得正族；独立 `Neg(k)` → `sub(0,k)`）
- [x] 负窗口红线用例：`Ref($close, -1)` 仍拒收（前瞻引用）
- [x] 真实候选回放用例：`-1 * ($close / Ref($close, 1) - 1)` → `sub(0,sub(div(close,ref(close,1)),1))`
- [x] `dump_mapping_table` / 文档串同步（「翻译器 v1 保守拒绝」表述移除）

## T2 深度上限 6 → 8（factors/dsl.py）

- [x] 先红后绿：`MAX_AST_DEPTH == 8`；深度恰 8 通过 / 深度 9 拒收（消息含「深度 9 > 上限 8」）
- [x] 既有三条上限并列违规用例迁移到新边界；RecursionError 转译消息随新上限
- [x] `prompts/factor_proposal.md`「AST 深度 ≤ 6」→「≤ 8」+ Langfuse 发布（production v4 实证）

## T3 转译合同失败重试（factors/harvest.py）

- [x] 先红后绿：第 1 次非法 JSON、第 2 次合法 → 转译成功且 `attempts=2`
- [x] 3 次均失败 → `transcription_error` 留痕含 `attempts=3`；整批不中断
- [x] 网关级异常不上抛改写：`complete_text` 抛错仍上抛（fail loud 语义不变；调用计数=1 钉死）
- [x] 载荷合同失败（缺 reason / 空 expression）同样走重试

## T4 按状态重放（factors/harvest.py + scripts/replay_factor_harvest.py）

- [x] 先红后绿：`retry_statuses={"untranslatable"}` 时命中候选重新入批（覆盖旧状态 +
      `retried_from` 留痕）；未命中状态不受影响
- [x] 重放优先于未处理候选进入限批截断
- [x] `retry_statuses=None` 幂等行为逐字不变（既有测试全绿）
- [x] ops 脚本：默认生产路径 / `--db --state --candidates --statuses --limit` 覆盖；
      打印状态迁移对照；`--dry-run` 只列候选不执行

## T5 测量与证据（复跑）

- [x] 临时库重放：6 条负数常量 + 3 条深度候选 → 新翻译裁决 + 过闸读数（含自由参数预算实记；
      review 修复后复测：恢复集不变，调用形态族参数各降 1）
- [x] 已拒 38 条复检：以当前面板/代码重放，**38/38 裁决不变**（无变化亦为读数；两轮复跑一致）
- [x] 2 条 `transcription_error` 实网重试（私有临时 state；attempts=1 / **attempts=2** 实证 + `retried_from` 落账）
- [x] 读数落档 → 验证报告 `tests/validation/2026-10-19-recover-factor-supply-losses-validation.md`

## T6 审查修复（双审查者 → 逐条处置）

- [x] Critical：重试面收窄（不再吞 resolver 配置类 `ValueError`）+ `IncompleteLLMConfigError` 用例
- [x] Important：调用形态（`Mul(-1,X)` 族）与中缀共用重排表 + 用例；Div k=1 条目勘误
- [x] Important：翻译器/脚本测试移入 CI 收集面（`tests/factors/`、`tests/`）
- [x] Important：ops 脚本护栏（状态白名单 / 严格读态 / limit 1-100 / 目标路径回显）
- [x] Minor：`(Previously:)` 注记、弱断言钉死、attempts 实测、重放保留 `qlib_expression`、文档措辞

## 收口

- [x] `uv run pytest tests/factors tests/test_replay_factor_harvest.py tests/test_api_factors.py tests/test_api_screening.py -q` 全绿；ruff 干净；mypy 改动模块无问题
- [x] `openspec validate recover-factor-supply-losses --strict` 通过
- [x] 验证报告（含 review 修复记录）
- [x] sync + archive 于 2026-10-19 执行（主规范 3 条 MODIFIED 落库；PR 随分支 recover-factor-supply-losses 提交）
