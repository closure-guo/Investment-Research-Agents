# Tasks: update-risk-free-rate-source

- [x] `risk_free_rates` 表 + 幂等迁移建表（存量库自动补齐，重跑不重复）——def271de
- [x] `AKShareClient.fetch_bond_yield_curve()` 可用：中债国债收益率曲线 1 年期，`_call_ak` 超时重试包装——a39608af + cafb1f71（紧凑日期契约）
- [x] `sync_risk_free_rates()` 落库幂等；日批挂钩于指标快照前，失败隔离（不阻断盯市/快照，仅 WARNING）——95c61ef8 / 91e4c129
- [x] `risk_free_series()` 回退链：库内 carry-forward → 全库无数据回退常数——95c61ef8
- [x] 夏普改逐日超额定义、α 改逐日 rf_t 残差；`compute_metrics_from_marks` 纯函数可选参 `rf_series`，None 时常数回退（向后兼容）——c1552a08
- [x] 回填脚本 `scripts/backfill_risk_free_rates.py`；历史 `agent_metrics_daily` 快照 as-of 重算——ff232057
- [x] 单测覆盖：rf 表幂等/回退链/夏普新口径/α 逐日 rf/日批失败隔离/as-of 重算；`uv run pytest tests/outcome/` 466 passed 全绿（全量 4033 passed；6 失败均为 `*_live` 真 LLM 用例的 provider 环境错误，与本 delta 路径无关）
- [x] `uv run ruff check` 通过 + `uv run mypy`（本 delta 模块零错误；akshare_client.py 存量 9 处 no-any-return 位于未触碰行）
- [x] 人工核读报告落 `tests/validation/2026-10-03-update-risk-free-rate-source-validation.md`（生产库拷贝演练：23 行真利率回填 + 12 行 as-of 重算 + 新旧夏普差异归因），不属交互类变更（无 E2E 门禁）
- [x] `openspec validate update-risk-free-rate-source --strict` 通过
