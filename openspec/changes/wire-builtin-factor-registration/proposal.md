# Proposal: wire-builtin-factor-registration

## Why

`openspec/specs/factor-library/spec.md` 的 Requirement「builtin 因子注册路径」要求系统内置五条手工因子基线包（20 日动量 / 5 日反转 / 换手率代理 / 20 日波动率 / 市值代理），以 `category=builtin` 注册入因子库（免 LLM 提案、同五道闸）。实现 `factors/builtin.py::register_builtins` 已就绪且幂等，但**全仓无生产调用方**（仅验收脚本调用，写隔离库）——生产因子库三表恒空。

后果（2026-10-15 生产实测，issue #345）：因子衰减监控 `checked=0`；因子提案月批真实数据 `proposed=20 · admitted=0`（2 条 LOW_COVERAGE + 18 条 WEAK_ICIR）；选股 L2 `RuntimeError: ValueError: 无有效 IC 信息，拒绝打分`——量化分析段（L2 打分）整段无法工作，且**无冷启动兜底**（LLM 提案路径在真实数据上全数被拒时，库永远为空）。

## What Changes

- 新增日批任务「内置因子注册」（`factor_builtin_register`，工作日 15:50——快照 15:30 与因子衰减监控 15:45 之后、结算链之前），走统一日批通道（调度 / 运行历史 / 手动补跑 / 单飞锁）。
- **存在性幂等**：库内已存在同表达式条目（任意状态，含 retired/degraded）→ 跳过（MUST NOT 复活 retired）；仅缺失条目进入准入。
- 缺失条目以快照库最近面板（取数口径沿用 `monitor` 单一来源）逐条过五道闸：过闸入库为 active，未过如实记逐条原因码；**面板不可得 → 上抛**（job 记 failed，不以空面板放行）。
- 摘要 `{registered, rejected, skipped, reasons}` 落运行历史（/ops 卡片可见）。
- `register_builtins` 增可选 `factors` 参数（默认全量五条）——任务只提交缺失子集；既有调用方语义不变。

## Capabilities

- **Modified Capabilities**: `factor-library`（MODIFIED「builtin 因子注册路径」：补注册入口、存在性幂等与 fail-loud 语义）

## Impact

- 代码：`src/finance_agent/factors/builtin.py`（新任务入口 + `factors` 参数）、`src/finance_agent/outcome/ops/jobs.py`（第 13 个 job）、`src/finance_agent/outcome/scheduler.py`（注册与重试包装）。
- 测试：`tests/factors/test_builtin_register_job.py`（新增）；`tests/outcome/test_ops_jobs.py` / `test_scheduler.py` 计数断言 12→13。
- 运维：`/ops` 日批时间线 +1 卡片（15:50）；真实面板上部分 builtin 可能被闸拒（长窗因子在 90 日面板的闸①覆盖口径问题已记 issue #345，另行裁决）——本 delta 只接线，准入结论如实落摘要。
