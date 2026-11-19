# Tasks: enforce-signal-freshness-at-planning

- [x] T1 规划前过滤层：来源状态重验（`STALE_SOURCE`）+ newest-wins（`SUPERSEDED_BY_NEWER`），skipped_details 留痕（单测：resolved 来源 flat 不执行旧仓 / 同标的新旧冲突 newest-wins / 合规信号回归护栏 三场景）
- [x] T2 回归：执行链全量 pytest + ruff check + ruff format --check + mypy（基线 98 不新增）（2026-11-20 新鲜复跑：270 passed / ruff+format 过 / mypy 98 基线零新增）
- [x] T3 生产实跑验证：下一执行日批核对 `STALE_SOURCE` 清场（600780.SH:2026-11-12 第二颗地雷同批拆除）与合规信号不受影响；报告落 `tests/validation/`，#596 附记录核关（11-18/11-19 四个生产批：STALE_SOURCE/SUPERSEDED_BY_NEWER 两机制全量留痕，600780 地雷两批零卖单，合规信号正常入池——见 2026-11-20 报告）
