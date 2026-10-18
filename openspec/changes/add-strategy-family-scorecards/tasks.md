# Tasks: add-strategy-family-scorecards

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T0 口径登记（先于代码）

- [ ] `docs/evals/metrics.md` §1 新增「策略族成绩单」小节（族定义 / 成本估算率 / 回撤算法 / 容量口径 / 极端阈值 / 胜率定位），与 design.md 一致

## T1 后端 scorecards 模块（`outcome/scorecards.py`）

- [ ] 先红后绿：注册表加载与「未注册数据源不出现」
- [ ] 先红后绿：四槽计算——毛/净双值、样本不足 null + `insufficient_sample`、极端样本明细、容量排除与 `no_liquidity_data`
- [ ] 先红后绿：回撤算法（已知序列对照手算值）
- [ ] 先红后绿：负例——缺数据不折算 0；胜率字段仅出现在展示载荷

## T2 端点（`api.py`）

- [ ] 先红后绿：`GET /api/scorecards` 全族返回 + 空数据族 null + 只读（零写入）
- [ ] 字段顺序：四槽先于胜率类字段

## T3 前端（战绩页策略族成绩单区块）

- [ ] vitest：四槽渲染 / 样本不足文案 / 空态 / 请求失败态
- [ ] `types.ts` + API 调用 + 战绩页挂载（四槽为主位）

## T4 E2E（默认套件，不 mock 业务接口）

- [ ] 新 spec：战绩页成绩单区块渲染 + 样本不足态（隔离库 fixture 注入）

## 收口

- [ ] `uv run pytest` 相关全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] 前端 `tsc --noEmit` + `vitest` 全绿
- [ ] `openspec validate add-strategy-family-scorecards --strict` 通过
- [ ] 人工验证（战绩页实跑）报告落 `tests/validation/`
- [ ] PR
