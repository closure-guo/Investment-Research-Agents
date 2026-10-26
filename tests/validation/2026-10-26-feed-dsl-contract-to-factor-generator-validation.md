# 人工验证报告: feed-dsl-contract-to-factor-generator

**日期**: 2026-10-26
**验证人**: Closure（agent 执行，用户批准立项）
**关联 delta**: openspec/changes/feed-dsl-contract-to-factor-generator/
**E2E 门禁**: 不适用（纯后端数据面 + distro 部署件，非交互类变更）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 契约块随导出载荷落盘 | 否（单测覆盖） | contract 含单一真相源复杂度值、算子/字段白名单、三条规则 | `test_build_payload_carries_dsl_contract_single_sourced` PASS；实跑 `export_factor_feedback.py` stdout `contract=yes` | ✅ |
| 闸口拒收计数聚合 | 否（单测覆盖） | gate → 条数按已裁决状态聚合 | `test_build_payload_gate_rejections_aggregated` PASS；实跑载荷 `gate0=3, gate1=8, gate2=136, gate4=37, gate5=6`（合计 190 = state rejected 数） | ✅ |
| 消费端渲染契约进首轮 prompt | 否（单测覆盖） | 契约段先于已探索清单 | distro 内实跑 `explored_feedback.py`：`# Hard contract...` 段在前，含三条规则、`max_ast_depth <= 8`、16 算子白名单、6 字段白名单、拒收计数，后接已探索清单 | ✅ |
| 契约缺失 fail-safe | 否（单测覆盖） | 旧版载荷仅渲染清单 | `test_missing_contract_falls_back_to_explored_only` PASS | ✅ |
| 载荷损坏 fail-safe | 否（单测覆盖） | 返回空串不阻断生成 | `test_corrupted_file_renders_empty` PASS | ✅ |
| 字符预算上界 | 否（单测覆盖） | 总长 ≤ MAX_CHARS+200 且截断标注 | `test_total_length_bounded_with_truncation_marker` PASS | ✅ |

## 实跑证据（2026-10-26 12:0x）

1. 部署：`wsl -d rdagent -u root` 覆盖 site-packages 消费端 → `py_compile` OK（运行中 rdagent 进程不受影响，下一进程生效）。
2. 宿主手动导出（worktree 新代码指向主检出真实状态文件）：
   `wrote .../explored_factors.json: explored=1208 returned=80 admitted=12 rejected=190 untranslatable=991 contract=yes`
3. distro 实跑渲染（`python3 .../explored_feedback.py`）首行起：
   ```
   # Hard contract for factor expressions (downstream DSL)
   Proposals violating any rule below are rejected BEFORE evaluation:
   - Numeric constants must be concrete numbers (e.g. write 1e-12); symbolic constants such as epsilon are inexpressible and the proposal is rejected.
   - Per-instrument time-series expressions only: cross-sectional (same-day cross-stock) operations such as cross-sectional percentile rank or market-wide averages are inexpressible.
   - Matrix operations, rolling regressions, and machine-learning pipelines are outside the expressible DSL.
   - Complexity limits: max_ast_depth <= 8, max_fields <= 3, max_free_params <= 3.
   - Expressible operators (Qlib form): Abs, Add, Corr, Delta, Div, Log, Max, Mean, Min, Mul, Rank, Ref, Sign, Std, Sub, Sum.
   - Expressible fields: $amount, $close, $high, $low, $open, $volume.
   - Historical gate rejections to date: gate0=3, gate1=8, gate2=136, gate4=37, gate5=6 (advisory).
   ```
4. 回归：`uv run pytest tests/factors/ -q` → **454 passed**；`ruff check` 全仓绿；`mypy src/finance_agent/factors/feedback.py` 无问题。

## 异常记录

- pre-commit 缓存锁被并发会话占用（PermissionError `.cache/pre-commit/.lock`）→ 按既有处置跑等价检查（ruff check + ruff format）后 `--no-verify` 提交。
- 容器重建前，日批 15:55 的容器内自动导出仍写旧版载荷（无契约块）；已用宿主手动导出补当日窗口。容器重建后自动导出即带契约（见 tasks 挂账项）。

## 结论

[x] 全部通过，可进入 merge；archive 待观察窗口（转译损失占比趋势）后收口
