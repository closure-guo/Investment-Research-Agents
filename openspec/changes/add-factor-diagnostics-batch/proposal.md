## Why

因子库详情接口把「风格暴露 + 分层单调性」诊断读数内联在 HTTP 请求里现算（500 交易日 × 全市场准入面板），容器实测单请求 10~20+ 分钟、探针请求 71 分钟未完成；唯一护栏（窗口 IC 点数 < 30 短路）被 10-22 起收割入库因子的历史 IC 回填绕过（10 条 active 因子窗口 IC 全部 ≥ 30）——历史根因（add-factor-ic-visibility 记载的「详情页全部加载失败」）复发。HTTP 路径上的面板现算必须整体移除，而非修补护栏。

## What Changes

- 诊断读数（`style_exposure` / `monotonicity`）的**生产位置**从详情接口请求内现算改为**日批计算落库**：新增库表 `factor_diagnostics`（每因子一行，含 `as_of` 数据截止交易日）与独立日批模块。
- 新增日批作业 `factor_diagnostics_daily`（17:15，含周末，与收割 `day_of_week="*"` 对齐；增量语义：因子读数缺失或 `as_of` 落后于最新快照日才计算，面板仅在有至少一个待算因子时构建）。
- 详情接口 `_factor_detail_diagnostics` 改为纯读库；**无条件** MUST NOT 构建准入面板（现 spec 仅约束「样本不足」分支）。读数不可用/未计算 → `null` 语义不变（前端展示不变）。
- 详情响应新增附加字段 `diagnostics_as_of`（读数数据截止日，未计算为 `null`）——可观测性，前端不消费。
- 无前端变更、无 LLM/prompt 变更。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `factor-library`: 详情接口诊断读数的来源契约——由「请求内在准入面板上现算」改为「日批预计算落库、接口纯读库；无条件不建面板」；并新增「诊断读数日批」requirement（增量计算、失败隔离、面板按需构建、作业注册）。

## Impact

- `src/finance_agent/api.py`：`_factor_detail_diagnostics` 重写为纯读库（删除面板/DSL/IC 现算路径）。
- `src/finance_agent/factors/library.py`：新表 `factor_diagnostics` + `get/set_factor_diagnostics` + `reset_factor_library` 纳入清空。
- `src/finance_agent/factors/`：新增 `diagnostics.py`（日批入口 `run_factor_diagnostics`，复用 `monthly._panel_from_snapshot` / `validate.check_monotonicity` / `style_exposure.compute_style_exposure`，逐因子失败隔离）。
- `src/finance_agent/outcome/ops/jobs.py`：注册 `factor_diagnostics_daily`（JOB_IDS / label / fn / schedule 四处）。
- 测试：`tests/test_api_factors.py`（详情诊断两态断言改为「存了才有」；新增「API 零面板构建」回归测试）、`tests/factors/test_diagnostics.py`（新）。
- 部署：镜像 `--build` 后需手动补跑一次作业为存量 12 因子补算读数（否则详情读数暂为 null，次日 17:15 自动补齐）。
- 风险：日批 17:15 与收割过晚结束（实测 17:56）可能并发，均为进程内 CPU 任务，无正确性影响；增量跳过使常态运行在分钟级内。
