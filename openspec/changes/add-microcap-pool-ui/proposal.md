# Proposal: add-microcap-pool-ui

## Why

小市值（microcap）通道自 16:45 日批上线以来只写不读：`selection_pool` 落了 `pool='microcap'` 的批次行与池行（含 `frontier_gate` 前置门禁与 `exec_constraints` 执行约束两个通道特有字段），但读侧全部接口缺省只取 main 档——microcap 精选池对用户完全不可见，通道观察模式（observation_only）的产出只能查库确认。选股页需要补上这一档的可见性。

## What Changes

- **读侧接口按档位参数化**：`GET /api/screening/pool` 与 `GET /api/screening/batches` 接受可选 `pool` 查询参数（`main` / `microcap`，缺省 `main`）；缺省行为与既有契约逐字一致，不破坏既有前端与测试。池响应新增批次级 `frontier_gate` / `exec_constraints` 透传（主档批次为 null）。
- **选股页池档位切换（v1 只展示）**：`/screening` 页精选池区块顶部新增「主档池 / 小市值池」切换；小市值视图复用池表结构（标的、股票名称、综合分、连续在榜、L3 判定、批次日期），并额外披露：前置门禁横幅（`observation_only` 时列出缺失前置）、执行约束（单票上限、滑点档位）、「仅观察、不向执行链产生订单」声明。
- **v1 不提供小市值池深度分析触发**：现有 16:22 自动池分析与「对精选池跑深度分析」按钮均只覆盖 main 档，本次不动 `pool_analysis` / 分析队列；小市值视图不渲染分析按钮。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `stock-screening`: 新增「精选池与批次历史按档位查询」（接口 `pool` 参数、缺省 main、非法值 422、microcap 空态）与「选股页池档位切换」（UI 契约：切换、门禁横幅、约束披露、观察声明、空态、主档不受影响）两个 ADDED 需求。
- `microcap-lane`: 新增「小市值池读侧可见性」需求（`frontier_gate` / `exec_constraints` 经池与批次读取透传；门禁语义在展示面的披露义务）。

## Impact

- **后端**：`src/finance_agent/api.py`（池/批次两端点加 `pool` 参数与校验）、`src/finance_agent/screening/pipeline.py`（`latest_successful_pool` 加 pool 参数）、`src/finance_agent/screening/store.py`（`list_batches` 加 pool 过滤；`list_selection_pool` 已支持 pool，仅接线）。
- **前端**：`frontend/src/pages/screening/ScreeningPage.tsx`（档位切换 + 门禁横幅 + 约束披露）、`frontend/src/types.ts`（响应类型扩展）。
- **测试**：后端 `tests/test_api_screening.py` 扩展档位用例；前端 vitest；E2E 新增交互 spec（stub 种子数据含 microcap 批次）。
- **不涉及**：写侧落库逻辑、自动池分析、调度器、执行链。
