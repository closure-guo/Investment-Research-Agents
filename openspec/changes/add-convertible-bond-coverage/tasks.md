# Tasks: add-convertible-bond-coverage

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T0 口径登记

- [ ] `docs/evals/metrics.md` §1 新增「可转债覆盖与条款事件」小节（字段口径 / 状态推导规则 / 清单准入）

## T1 标的与行情落库

- [ ] 先红后绿：标的 upsert（幂等）/ 日行情 upsert（交易日+代码唯一）
- [ ] 先红后绿：抓取失败留痕；字段缺失如实置空（MUST NOT 估算）

## T2 条款事件与状态推导

- [ ] 先红后绿：事件落库（事件类型+代码+公告日 唯一）
- [ ] 先红后绿：状态推导纯函数——倒计时/已触发对照手算（含交易日历）
- [ ] 先红后绿：要素缺失 → 不可判定 + 注明缺失字段

## T3 清单接线与日批

- [ ] 先红后绿：清单构建 `source=cbb` 分支（含风险要素；零 LLM 调用）
- [ ] 先红后绿：端点按 `source` 过滤
- [ ] `outcome/scheduler.py` 注册抓取/事件 job（幂等、失败留痕、进 ops 清单）

## 收口

- [ ] `uv run pytest` 相关全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] `openspec validate add-convertible-bond-coverage --strict` 通过
- [ ] 人工验证（清单实跑核对：条目字段与倒计时手算抽样一致）报告落 `tests/validation/`
- [ ] PR
