# Tasks: add-microcap-lane-guardrails

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T1 含退市股池（前置 ①）

- [x] 先红后绿：退市清单抓取与落库（幂等；字段缺失如实标注）
- [x] 先红后绿：`security_master` 扩展（退市标记 + 退市日）与迁移兼容（既有读取路径不破坏）
- [x] 先红后绿：池构建含退市标的——防幸存者偏差回归（先红：无逻辑时失败）+ 退市后不可见
- [x] 退市标的价格历史补采通道（可得范围内；缺失如实标注）

## T2 档位化（前置 ② 消费）

- [x] 先红后绿：`constraints.py` 档位（main 默认逐字节不变；microcap 阈值 1000 万）
- [x] 先红后绿：过滤层/批次编排按档位执行（越档场景：2000 万标的两档判定相反）

## T3 通道批次与前置门禁

- [x] 先红后绿：批次产出含 `pool=microcap` 与建议约束字段
- [x] 先红后绿：前置门禁（退市池就绪 + regime 数据就绪）——缺前置 `observation_only` + 披露缺失项；齐备标记可执行候选；均不产生订单
- [x] `outcome/scheduler.py` 注册通道日批（幂等、失败留痕、进 ops 清单）

## 收口

- [x] `uv run pytest` 相关全绿；`ruff check` 干净；新文件 mypy 零新增
- [x] `openspec validate add-microcap-lane-guardrails --strict` 通过
- [x] 人工验证（批次数据核对：主档零变化 + 通道批次字段/门禁状态）报告落 `tests/validation/`
- [x] PR

> 2026-10-23 回填：实现随 `cceb1a31`（#403）并入 main；`microcap_screening_daily` 每日 16:45 运行中，
> 验证报告 `tests/validation/2026-10-18-microcap-lane-validation.md`。
> **运维遗留**：`security_master.delist_date` 尚未回填（5618 行全空），故门禁仍
> `observation_only:missing=delist_pool`、通道池为 0 行；补采任务见
> `docs/audit/2026-10-23-screening-funnel-review.md` §5。
