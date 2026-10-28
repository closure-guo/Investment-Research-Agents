# Proposal: add-pool-stock-name-display

## Why

选股页精选池表格的「标的」列只展示六位代码（如 `600519`），没有股票名称。名字在数据源头是有的
（`market_snapshot` 表含 `name` 列，日批快照自带），但选股流水线落池行时只保留了 9 个决策字段，
名称在这一步被丢弃；池接口原样透传、前端无字段可渲染。用户浏览精选池需要逐只查代码才能知道
标的是什么，可读性缺口明确。

## What Changes

- `selection_pool` 表新增 `stock_name` 列（`TEXT`，可空）：新建库 DDL 直接含列，存量库经
  幂等 `ALTER TABLE ADD COLUMN` 迁移补齐（迁移单点沿用 `_BATCH_COLUMN_MIGRATIONS` 同款模式）。
- 选股日批落池行时从 L1 快照记录透传 `name`（主档与小票通道同一落库契约，自动同享）；
  快照行 name 缺失时池行 `stock_name` 落空串并记 warning——**展示字段不阻断决策字段**，
  批次不因此失败（与 `_pool_row_payload` 决策字段的 fail loud 语义区分开，见 design）。
- 池接口 `/api/screening/pool`（与批次历史池读取 `list_selection_pool`）池行透传 `stock_name`。
- 选股页精选池表格新增「股票名称」列：名称缺失时渲染 `—`（与既有行为一致退化，非回归）。
- **不做**（明确排除）：
  - L2 全量名单（`l2_candidates`）与 triage 决策表不加名字——用户可见面是精选池，全量榜
    无展示位；
  - 池分析自动会话的 `display_name` / 日报文案带名字（`pool_analysis.py` 传空串的另一处
    口径，另行提案）；
  - 存量历史池行回填——池行按日易变、当日 upsert 幂等，次批自动补齐，历史行名称缺失按
    `—` 展示即可。

## Capabilities

### Modified Capabilities

- `stock-screening`：ADDED「精选池行携带股票名」——落库透传来源（同日快照 name）、缺名
  降级语义（空串 + warning、批次不失败）、主档/小票通道同契约、池接口透传、存量行可空兼容。
- `frontend`：MODIFIED「选股页」——精选池表格列清单从「标的、综合分、连续在榜天数、L3
  verdict 与 reason、批次日期」扩为含「股票名称」；名称缺失渲染 `—`。

## Impact

- 代码：
  - `src/finance_agent/screening/store.py`：`SCREENING_DDL` 的 `selection_pool` 加列；
    `_POOL_COLUMN_MIGRATIONS` 新增幂等 ALTER；`_pool_row_payload` 校验/携带 `stock_name`；
    `record_screening_batch` INSERT 列集合扩一项；`list_selection_pool` SELECT 加列；
    存量库重建语句序列（`_POOL_TABLE_REBUILD_SQL`）同步含新列。
  - `src/finance_agent/screening/pipeline.py`：L1 阶段构建 `symbol → name` 映射，
    池行 dict 增加 `stock_name`；keep 标的缺名时记 warning。
  - `src/finance_agent/api.py`：无改动（池行 dict 透传，新键自动出现）。
  - `frontend/src/pages/screening/ScreeningPage.tsx`：池行类型加 `stock_name`，表格加列。
- 测试：store 往返 + 旧库迁移用例、pipeline 池行带名/缺名降级用例、API 池行字段用例、
  前端组件测试（名称列渲染 / 缺名 `—`）、e2e 选股页 spec 补名称列断言。
- 兼容性：存量池行 `stock_name` 为 NULL → 接口返回 `null` → 前端渲染 `—`（与现状
  等价）；无配置变更、无新增依赖；同日手动补跑 upsert 自动补名，无需回填脚本。
- 部署：纯应用代码 + 幂等 DDL 迁移；生产生效须容器重建（`--build backend`，沿用部署纪律）。
