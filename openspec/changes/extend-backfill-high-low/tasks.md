# Tasks: extend-backfill-high-low

> TDD 先红后绿；重跑为长任务（incident 043 纪律）。

## T1 取数层（akshare_client.py）

- [ ] 先红后绿：`fetch_symbol_history_raw_qfq` 输出八列（+最高/最低，raw 口径）；东财双口径腿
      必需列集与合并列同步扩展；新浪腿 `high`/`low` 归一映射
- [ ] 先红后绿：任一源缺 最高/最低 列 → 该腿判失败（回退另一源；两源皆缺 → None，不产半口径帧）
- [ ] 既有断言更新（`_EM_CONTRACT_COLUMNS` 等）+ 新浪路径列断言扩展

## T2 回填脚本（backfill_market_snapshot.py）

- [ ] 先红后绿：`_history_rows` 行映射 `high`/`low` 取「最高/最低」（替代硬编码 None）
- [ ] 先红后绿：断点续跑齐备判据扩为 amount + high + low 全覆盖（缺者重取 upsert 补齐；
      新增用例：amount 齐备但 high/low 缺 → 不跳过）
- [ ] 既有 resume/字段映射用例按八列契约更新

## T3 收口

- [ ] `uv run pytest tests/data/test_market_data_fallback.py tests/scripts/test_backfill_market_snapshot.py -q` 全绿；ruff / mypy 干净
- [ ] `openspec validate extend-backfill-high-low --strict` 通过
- [ ] 全量重跑（16 进程并行，--symbols-file 分片；不硬杀）→ high/low 覆盖率复查
      （目标：1–9 月覆盖从 0.0% 升至 >99%；读数落验证报告）
- [ ] 验证报告落 `tests/validation/` + PR；sync + archive 在重跑读数复核后执行
