# Tasks: add-event-driven-lane

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T0 口径登记（先于代码）

- [ ] `docs/evals/metrics.md` §1 新增「业绩预告事件线」小节（字段口径 / 分诊合同 / 入场与 horizon 口径 / 家族隔离声明）

## T1 抓取与落库

- [ ] 先红后绿：字段映射（含 `available_at`=公告日期）/ 幂等 upsert / 重复抓取单条
- [ ] 先红后绿：时点截断（T 日判断看不到 T 日之后公告）
- [ ] 先红后绿：抓取失败留痕且不冒充成功

## T2 LLM 分诊

- [ ] 先红后绿：合同解析与拒收（非法 verdict / 超长 reason / 缺证据）
- [ ] 先红后绿：单条失败降级 `in_line` + warning 不阻断批次
- [ ] prompt `src/finance_agent/prompts/event_triage.md` 落库 + `deploy_prompts.py` 发布（部署后跑 prompt 门禁核对）

## T3 观察清单与队列接线

- [ ] 先红后绿：outperform 入清单、in_line/risk 不入
- [ ] 先红后绿：发起深度分析复用 `screening/queue.py`（并发同源、关联 session、重复点击不重复入队）

## T4 记账与结算

- [ ] 先红后绿：入场口径（T+1 开盘、停牌/一字板顺延留痕）
- [ ] 先红后绿：到期结算与参考价缺失保持未结算
- [ ] `outcome/scheduler.py` 注册抓取/分诊/结算 job（幂等、失败留痕、进 ops 任务清单）

## T5 成绩单注册与端点

- [ ] scorecards 注册 `family=event`（注册表扩展；家族隔离测试：不混入 decision 统计）
- [ ] 先红后绿：清单只读端点（排序/空态/零写入）

## T6 前端（选股页事件观察区块）

- [ ] vitest：清单渲染三态 / 发起深度分析交互 / 空态 / 失败态 / 口径注释
- [ ] `types.ts` + API 调用 + 选股页挂载

## T7 E2E（默认套件，不 mock 业务接口）

- [ ] 新 spec：事件观察区块渲染 + 发起分析 + 空态（隔离 `EVENTS_DB_PATH` fixture 注入；LLM 走 TESTING stub）

## 收口

- [ ] `uv run pytest` 相关全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] 前端 `tsc --noEmit` + `vitest` 全绿
- [ ] `openspec validate add-event-driven-lane --strict` 通过
- [ ] 人工验证（选股页实跑 + 分诊质量抽检）报告落 `tests/validation/`
- [ ] PR
