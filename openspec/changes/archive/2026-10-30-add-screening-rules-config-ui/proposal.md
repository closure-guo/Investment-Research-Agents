# Proposal: add-screening-rules-config-ui

## Why

选股规则的可调口径（L1 上市天数下限 60、L1 近 20 日日均成交额下限 5000 万、L2 Top-N 100）目前
只存在于 `screening/constraints.py` 与 `screening/pipeline.py` 的模块常量中：界面上没有任何
地方能看到「当前生效的选股规则是什么」，调整口径必须改代码并重新部署。设置中心已有
「批量分析并发上限」这一 file > env > default 配置先例（delta add-analysis-concurrency-setting），
选股规则沿用同一模式补齐配置面。

## What Changes

- 新增选股规则运行时配置，可配置项恰为三项：
  `min_trading_days`（L1 上市交易日下限，默认 60）、
  `min_avg_amount_20d`（L1 近 20 日日均成交额下限，元，默认 5000 万）、
  `l2_top_n`（L2 Top-N，默认 100）；来源优先级 配置文件 > 环境变量 > 默认值。
- 新增读取/保存端点 `GET/PUT /api/settings/screening-rules`（非法值 400、原子落盘、
  file_status 如实披露），模式对齐 `analysis_concurrency.py`。
- 主档选股日批（排程与手动补跑同口径）改为批次开始时解析生效配置；**运行中批次不受影响**，
  下一批次自动应用；小票通道档位（`MICROCAP_THRESHOLDS`）与 L3、稳定性门槛口径不受影响。
- 设置中心（`/settings`）新增「选股规则」分区：三项规则的生效值 + 来源披露 + 区间校验 +
  保存反馈 + 生效时机说明；成交额以「万元」呈现与录入（后端仍存元）。
- **不做**（明确排除）：
  - 稳定性门槛连续天数 / 滚动窗口天数——在飞 delta `fix-stability-gate-mode-axis` 正在重构
    该口径的函数签名，避免同域撞车，待其归档后另行提案；
  - 小票通道档位（观察通道记录字段）；
  - 执行层风控上限（`MAX_SINGLE_POSITION` / `MAX_TOTAL_POSITION`，属交易执行规则非选股规则）；
  - L3 LLM 粗筛开关（契约固定「失败按 keep 降级」，无开关位）。

## Capabilities

### Modified Capabilities

- `stock-screening`：
  - MODIFIED「约束单一来源」——constraints.py 保持为**默认值**单一来源；主档日批生效值改经
    运行时配置解析（未覆盖时与默认值逐位一致）；小票通道与执行层 import 路径不变。
  - ADDED「选股规则运行时配置」——三项可配置项、来源优先级、校验与 file_status 披露、
    生效时机（批次开始解析、运行中批次不受影响）、端点契约。
- `settings-center`：MODIFIED「设置中心页路由与左侧垂直导航」——分区清单从六大更新为九大
  （补齐既有「因子研发配置」「评估运维」两分区的事实枚举，新增「选股规则」）。
- `frontend`：ADDED「选股规则配置分区」——分区入口、三项规则的展示/编辑/保存/拦截行为、
  万元口径换算、生效时机与加载失败隔离。

## Impact

- 代码：
  - 新建 `src/finance_agent/screening/rules_config.py`（resolve/save/validate，镜像
    `analysis_concurrency.py`）；
  - `src/finance_agent/screening/pipeline.py`：主档批次开始时 resolve 配置 → 显式
    `FilterThresholds` 传入 L1 两消费点、`l2_top_n` 替换 `L2_TOP_N` 常量读取；
  - `src/finance_agent/api.py`：两个设置端点；
  - `frontend/src/pages/settings/SettingsCenterPage.tsx`（导航项）+ 新建
    `frontend/src/pages/settings/panes/ScreeningRulesPane.tsx`。
- 测试：`tests/screening/test_rules_config.py`（新建）、`tests/test_analysis_concurrency.py`
  同族的 API 测试、`tests/screening/test_pipeline.py` 接线用例、前端组件测试、e2e 设置分区 spec。
- 兼容性：无配置文件、无环境变量时全部行为与现状逐位一致（默认值 = 既有常量）；
  配置文件为运行时读，不引入新依赖、不改 schema。
- 部署：纯应用代码，无迁移脚本；生产生效须容器重建（沿用部署纪律，非本 delta 范围）。
