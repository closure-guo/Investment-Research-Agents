# Tasks: add-crowding-regime-positioning

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T0 口径登记（先于代码）

- [ ] `docs/evals/metrics.md` §1 新增「市场拥挤度与总仓位系数」小节（四指标口径 / 分位窗口与最小样本 / 触发规则 / 系数映射 / 陈旧口径），与 design.md 一致

## T1 regime 模块（`outcome/regime/`）

- [ ] 先红后绿：指标计算纯函数——最小记录集对照手算（含小市值分组、停牌/零成交剔除、封板计数）
- [ ] 先红后绿：分位计算与 `insufficient_history`（样本 59/60/61 边界）
- [ ] 先红后绿：落库幂等（同日重跑单行覆盖）；快照缺失不产行
- [ ] 先红后绿：判定纯函数——normal/elevated/danger 三态 + reasons + 可复现
- [ ] 先红后绿：配置加载三态（缺文件回落 + WARN / 损坏 fail loud / 合法覆盖）
- [ ] 存储访问器：latest 行 + 陈旧判定（7 自然日两侧）

## T2 日批接线

- [ ] 先红后绿：`outcome/scheduler.py` 注册新 job（快照后执行、幂等、失败留痕）并进 ops 任务清单

## T3 执行链接线

- [ ] 先红后绿：`aggregate_targets` 系数参数——缩放 / 0 清仓 / `REGIME_SCALE` 逐票留痕 / 系数 1.0 无痕 / 默认行为逐字节不变（既有测试全绿）
- [ ] 先红后绿：`driver.py` 读取最新 regime + 判定；缺失/陈旧 → 1.0 + 摘要 `no_regime_data`；库损坏同缺失处理且日批不失败

## T4 端点 `GET /api/regime`

- [ ] 先红后绿：有记录返回判定+指标+历史+`age_days`/`stale`；无记录/损坏 → 200 + `available=false` + 原因码；只读零写入

## T5 前端（选股页市场温度计区块）

- [ ] vitest：三态渲染（徽章/系数/指标/曲线/理由）/ 陈旧披露 / 空态（missing 与 corrupt 文案区分）/ 无控制件
- [ ] `types.ts` + API 调用 + 选股页挂载

## T6 E2E（默认套件，不 mock 业务接口）

- [ ] 新 spec：选股页温度计区块渲染 + 空态（隔离 `REGIME_DB_PATH` fixture 注入；默认 config 补 env）

## 收口

- [ ] `uv run pytest` 相关全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] 前端 `tsc --noEmit` + `vitest` 全绿
- [ ] `openspec validate add-crowding-regime-positioning --strict` 通过
- [ ] 人工验证（选股页实跑 + 执行链摘要核对）报告落 `tests/validation/`
- [ ] PR
