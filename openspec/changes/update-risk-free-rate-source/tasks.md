# Tasks: update-risk-free-rate-source

- [ ] `risk_free_rates` 表 + 幂等迁移建表（存量库自动补齐，重跑不重复）
- [ ] `AKShareClient.fetch_bond_yield_curve()` 可用：中债国债收益率曲线 1 年期，`_call_ak` 超时重试包装
- [ ] `sync_risk_free_rates()` 落库幂等；日批挂钩于指标快照前，失败隔离（不阻断盯市/快照，仅 WARNING）
- [ ] `risk_free_series()` 回退链：库内 carry-forward → 全库无数据回退常数
- [ ] 夏普改逐日超额定义、α 改逐日 rf_t 残差；`compute_metrics_from_marks` 纯函数可选参 `rf_series`，None 时常数回退（向后兼容）
- [ ] 回填脚本 `scripts/backfill_risk_free_rates.py`；历史 `agent_metrics_daily` 快照 as-of 重算
- [ ] 单测覆盖：rf 表幂等/回退链/夏普新口径/α 逐日 rf/日批失败隔离/as-of 重算；`uv run pytest` 相关套件全绿
- [ ] `uv run ruff check` + `uv run mypy` 通过
- [ ] 人工核读报告落 `tests/validation/`（新旧夏普/α 对照 + 差异归因），不属交互类变更（无 E2E 门禁）
- [ ] `openspec validate update-risk-free-rate-source --strict` 通过
