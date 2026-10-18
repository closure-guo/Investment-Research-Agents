# 人工验证报告: add-fdr-candidate-ledger（issue #355 使能层）

**日期**: 2026-10-18
**验证人**: ZCode 验证 runner（单测 + 生产面板容器内实跑）
**关联 delta**: openspec/changes/add-fdr-candidate-ledger/
**E2E 门禁**: 不适用（纯后端数据面，前端零变更）——真实链路由容器内对生产面板实跑补足
**执行环境**: worktree `.worktrees/fix-fdr-ledger` @ 基于 main 86cdbead；容器 `finance-agent-backend:latest` + 生产 `data/market_snapshot.db` 面板 + 生产库副本临时因子库

## 缺口与实现（issue #355 使能层）

**缺口**：闸③ p 值无落点（被拒候选无 factors 行、`factor_events` 记不了；采纳候选的 p 值只存在于内存 `GateResult.detail`）→ 跨批次 FDR 族定义不可查询，「全局账本」方向在数据面不可实施。

**实现**：
1. `factor_admission_ledger` 表（幂等 DDL）：batch_id / source / expression / 闸③ p_value·p_adj·q·n_hypotheses / admitted / factor_id / rejected_gate / created_at；
2. `admit_candidates` **单点记账**（`_record_ledger`）——所有入口天然覆盖，杜绝逐入口接线遗漏；记账失败只记日志、不改准入结论（观测面纪律）；
3. 四入口传 source：`monthly_broad`/`monthly_deep`/`builtin`/`harvest`（external 默认 `external`）；
4. `cumulative_fdr_preview` 纯函数（跨批家族 BH 反事实）+ `ledger_summary` 读数；
5. `scripts/factor_fdr_report.py` 只读读数脚本；`reset_factor_library` 纳入账本清空。

**明确未做（披露）**：执行层不实施——强制全局校正 / 批内更严 q' / 显式接受残余风险，属 issue #355 三方向「供裁决」；本 delta 只保证账本可写可查，读数 MUST NOT 改变任何准入结论。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | 账本 DDL/批量单事务/汇总/复位 | `test_admission_ledger_schema_and_roundtrip` / `test_reset_factor_library_clears_admission_ledger` | ✅ |
| 2 | 单点记账（builtin 真管线路经） | `test_builtin_registration_writes_admission_ledger`：5 候选各一行、同 batch/source、闸②拒收 p=NULL、入库行 p 非空带同批族读数 | ✅ |
| 3 | 反事实纯函数（NULL 不入家族/只读/空家族） | `test_cumulative_fdr_preview_pure_function` | ✅ |
| 4 | 回归 | `tests/factors/`（含新 4 例）**334 项绿**；ruff/mypy 零告警 | ✅ |
| 5 | **生产面板容器内实跑**（真实批 + 真实读数） | 生产 `market_snapshot.db` 面板 + 库副本：注册摘要 `rejected=5`；账本 5 行 `[builtin] size=5 tested=1 admitted=0`；反事实 `家族 1 → 保持显著 1（div 因子 p=0.001，未入库）` | ✅ |
| 6 | 生产库只读读数 | `scripts/factor_fdr_report.py` 对生产 `data/factor_library.db`：空账本正常输出（表已按幂等 DDL 建立） | ✅ |

## 未达成项 / 披露

1. **执行层未实施**（如上）；读数已就位：owner 裁决任一方向后，本账本即为「全局校正 / 批内折算 / 残余风险留档」的共同数据底座；
2. 生产库在本次容器内只读读数时由幂等 DDL 建出账本空表（CREATE IF NOT EXISTS，与合并后首个日批行为一致）——如实披露；
3. 预检失败候选（DSL 不可解析）不入账本（从未进入五道闸，非检验族成员）——族定义与 `admit_candidates` 输入域一致，已在 spec 场景注明。
