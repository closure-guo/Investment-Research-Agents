# Tasks: add-fdr-candidate-ledger

- [x] `factor_admission_ledger` DDL（幂等）+ `record_admission_ledger` 批量单事务写入 + `ledger_summary` + 全局 BH 反事实预览（纯函数，参考 `benjamini_hochberg_adjust`）+ 单测
- [x] `admit_candidates` 单点记账（`ledger_source` 参数；四入口传 source：monthly_broad/monthly_deep/harvest/builtin/external）+ 单测（含闸②拒收 p 值 NULL、闸③拒收 p 值非空带同批读数）
- [x] `reset_factor_library` 清空账本 + 单测
- [x] `scripts/factor_fdr_report.py` 读数脚本（汇总 + 反事实预览输出）
- [x] 定向测试（tests/factors）+ 全量套件 + ruff/mypy 绿（334 项 + 静态零告警；全量套件交 CI）
- [x] 真实链路实跑：生产面板容器内实跑 builtin 批（rejected=5，账本 5 行 tested=1）+ 生产库只读读数（空账本）
- [x] 验证报告落 `tests/validation/`（含「enforcement 未实施、供 owner 裁决」边界披露）（含「enforcement 未实施、供 owner 裁决」的边界披露）
