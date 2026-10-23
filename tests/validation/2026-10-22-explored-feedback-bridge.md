# 跨会话因子记忆桥（回喂 RD-Agent）上线记录（2026-10-22）

- 目的：RD-Agent 的 feedback 只在同一 `trace`（会话）内传递，新会话历史为空 → 同一经典因子
  换名字反复提案（`mom20`/`mom_20d`/`MOM_20D`/`momentum_20d` 四条同式）。
  本桥把本系统已探索/已裁决因子导出到生成侧，在**首轮提案**注入「已探索清单」。
- 性质：附加产物 + 生成侧只读注入；不改五道闸、不改阈值、不改收割裁决逻辑。

## 组成

| 侧 | 文件 | 作用 |
|---|---|---|
| 本仓库 | `src/finance_agent/factors/feedback.py` | 状态 + 候选 → 清单载荷（epsilon 归一、去重、排序、截断 80） |
| 本仓库 | `scripts/export_factor_feedback.py` | 手动导出 CLI（原子写 `data/factor_research/explored_factors.json`） |
| 本仓库 | `src/finance_agent/factors/harvest.py` | 每次收割成功后台自动刷新清单（best-effort，失败仅告警） |
| 本仓库 | `tests/scripts/spike/explored_feedback.py` | distro 侧读取端源码（渲染 ≤9000 字符，缺失/损坏返回空串） |
| 本仓库 | `tests/scripts/spike/patch-explored-feedback.py` | 幂等补丁：备份 + 注入 import 与首轮块 + `py_compile` |
| distro | `.../rdagent/scenarios/qlib/proposal/explored_feedback.py` | 已部署（md5 与仓库源一致） |
| distro | `.../rdagent/scenarios/qlib/proposal/factor_proposal.py` | 已打补丁（备份 `.bak-20261022-explored`） |

## 补丁内容

`QlibFactorHypothesisGen.prepare_context` 中，仅在 `len(trace.hist) == 0`（会话首轮）把
`render_explored_block()` 追加到 `hypothesis_and_feedback`；会话内后续轮次仍由 trace 自身反馈承担。
清单为空/文件缺失时追加为空串（fail-safe）。

## 验证

- 本仓库：`uv run pytest tests/factors/test_feedback.py tests/factors/test_harvest.py -q` → **35 passed**；
  `ruff check` 通过；`mypy src/finance_agent/factors/feedback.py` 通过。
- distro：`explored_feedback.py` 渲染 9000 字符 / 44 条（80 条载荷按预算截断）；
  冒烟 `prepare_context` 首轮上下文 9075 字符且含 `Cross-session memory`；
  `from ...factor_proposal import QlibFactorHypothesisGen` 导入成功。
- 隔离：`tests/factors/test_harvest.py` 的 autouse fixture 同时把
  `FACTOR_FEEDBACK_PATH` 指向 `tmp_path`（此前一次测试运行曾误写生产桥文件，已修复并重新生成）。

## 生效范围与刷新

- **当前运行中的会话（12:30 启动，pid 1546）不受影响**：Python 模块已导入，补丁在下一个
  `rdagent fin_factor` 进程（下一会话）才生效。未重启会话，避免打断运行中的研发。
- 清单刷新：每次 `factor_harvest_daily` 成功后自动重写；也可随时手动
  `uv run python scripts/export_factor_feedback.py`。

## 激活记录（2026-10-22 14:25）

在用户确认后，按「本轮循环结束点重启」执行（走官方重启通道，不手杀进程）：

| 时刻 | 事件 |
|---|---|
| 14:22:48 | 值守启动，基线 `session-20261022-123013.log` / loop 14 |
| 14:25:19 | 检测到 loop 14→15（第 14 轮结束），`POST /api/factor-research/restart`，`restart_pending=true` |
| 14:25:32 | 代理回执 `result=restarted` |
| 14:25:29 | 新进程 `rdagent fin_factor`（pid 10072）启动——晚于补丁部署时间 14:12 |
| 14:25:30 | 新会话 `session-20261022-142530.log` 开始，`alive=true`、`actual_state=running` |

**首轮 prompt 实证**：新会话日志第 163 行起出现注入块，条目含
`RANGE_EXPANSION_20D / range1 / volcv20 / volratio20 / Mom120 …`，按 9000 字符预算截断为 46 条。
即：生成侧首轮确实读到了跨会话记忆。

代价：12:30 会话（已跑 14 轮）的会话内 trace 随重启丢弃；工作区因子、候选、日志保留。

## 回滚

```bash
wsl -d rdagent -u root -- bash -c "cp /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/factor_proposal.py.bak-20261022-explored /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/factor_proposal.py"
```

回滚后 `explored_factors.json` 可保留（无消费者，零影响）。

## 风险与观测点

- 首轮 prompt 增加约 9k 字符（≈2.5k tokens）；清单上限 80 条，超出按预算截断。
- 清单是「已探索」快照，可能滞后一次收割——只会少列，不会列错（fail-safe 语义）。
- 观测：下一会话首轮的 `hypothesis_and_feedback` 应含 `Cross-session memory`；若提案仍大量
  重复同一表达式，说明注入未生效或模型忽略，需查 `FACTOR_FEEDBACK_PATH` 与 blk 长度。
