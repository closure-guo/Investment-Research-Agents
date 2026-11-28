# extend-factor-library-coverage P2 验证报告（2026-11-28）

**一句话**：参考数据面已上线并完成历史回填（`float_cap` / `turnover` 日期覆盖率 99.8–99.9%），闸①字段预检 / 桶归属 / builtin 七条 / 选股缺口披露按 spec 落地；行业门类探针成功率仅 **72% < 90% 门禁**，全量回填按 spec 主动不执行（`industry_l1` 仍 0 覆盖，`ind_neutralize` 类因子继续被闸①拒收）。

- 代码：PR #630 → main `32c8d034`；部署 = 主检出 `pull --ff-only` + `docker compose restart backend`（重启前 `GET /api/sessions` 无 running、`GET /api/v1/ops/jobs` 无 running）。
- 交互类判定：本 change **无前端 UI / SSE / 会话状态变更**（仅后端日批摘要字段），不触发 E2E 人工验证门禁；CI 的 E2E stub 作业仍为绿。

## 1. 测试与门禁

| 门禁 | 结果 | 备注 |
|---|---|---|
| `pytest tests/data tests/outcome tests/screening tests/factors` | 全绿（2439 passed @ 中途；最终 factors 651 / screening 296 单独复跑） | 含 Task A–E 全部新用例 |
| `pytest tests/scripts/test_backfill_*.py` | 13 passed | 默认门禁排除 `tests/scripts`，显式执行 |
| 全量 `pytest tests/ --ignore=tests/scripts -m "not live"` | **7590 passed, 6 skipped, 3 failed + 25 errors** | 28 项全部为宿主 `uv` 缓存权限（`sdists-v9/.git: 拒绝访问 os error 5`）；**main 检出正控逐条复现**（与 P1 同类环境性归因） |
| `ruff check` / `ruff format --check` | 全绿（src/tests/scripts 全树） | 顺带修正 P1 遗留两个测试文件格式 |
| `mypy`（改动文件定向） | 0 错误 | 全树存量 64 项错误在 26 个非本 PR 文件（第三方 stub/langgraph 漂移；CI `mypy src/ || true` 非阻断） |
| `openspec validate extend-factor-library-coverage --strict` | 通过 | 1.6.0 |
| prompt-deploy-consistency | `_verify_prompt_sync` = 0 mismatch | `factor_proposal` 已发布（1589 chars） |

## 2. 参考数据历史回填（容器内，2024-09-02 .. 2026-11-27）

`docker compose exec -T backend python scripts/backfill_factor_ref.py --start 2024-09-02 --end 2026-11-27 --sleep 0.4`

摘要：`symbols_ok=5608`、`symbols_failed=369`（快照无覆盖或新浪失败，均不落半行）、`skipped_resumed=13`（先导 20 票冒烟已覆盖）、`rows_written=2,942,506`、`skipped_rows=1458`（Sina 帧有日期但快照无行）、覆盖 `2024-09-02..2026-11-27`。

**覆盖率（侧表完整行 / 当日 market_snapshot 行）**：

| 日期 | snapshot 行 | ref 完整行 | 覆盖率 |
|---|---|---|---|
| 2024-09-02 | 5325 | 5322 | 99.94% |
| 2026-11-27 | 5587 | 5575 | 99.79% |

侧表最终 2,949,533 行 / 5,621 票。

**口径抽样（600519，2026-11-27）**：回填 `float_cap=1,840,570,146,048` 元、`turnover=0.0031845`；腾讯批量同日 `float_cap=1,840,570,000,000` 元（差 8e-8）、`turnover=0.0032`（腾讯 4 位小数舍入，差约 0.5%）。`float_cap = outstanding_share(1,250,081,601) × 快照 close_raw(1472.36)` 逐值可复算。

## 3. 行业门类探针（门禁未过，全量按 spec 不执行）

`python scripts/backfill_industry.py --probe 50 --limit 50 --sleep 0.2`

- `attempted=50`、`found=36`、`failed=0`（14 只 cninfo 无行业条目）、**成功率 72.0% < 90% 门禁** → `aborted=true`、`written=0`（未写库）。
- 耗时 279s（约 5.6s/票；东财主源不可用后逐票降级 cninfo）——即使门禁通过，全量 5990 票约需 9 小时。
- **结论**：`industry_l1` 覆盖维持 0/5990；`ind_neutralize` 类候选在闸①按 `missing_groups=["industry"]` 拒收（spec 既有语义）。周一主表刷新分支的增量补抓同样受源质量限制（每次 ≤50 只，失败仅 WARN）。后续需换行业源（如申万行业成分）或提升 cninfo 命中率后重跑探针。

## 4. 生产行为读数

**部署 smoke**：容器内 `OPS=40`、`BUILTIN_FACTORS=7`、`CATEGORIES=9`、`fetch_market_ref_bulk` / `fetch_symbol_share_history` 均存在；`/api/sessions` 200。

**前置迁移（update-harvest-factor-category 遗留项，本 delta §10 前提）**：容器内 `scripts/migrate_factor_category.py` → `migrated=12`；幂等复跑 `migrated=0`。active 12 条桶分布 = 量与流动性 5 / 波动率 3 / 量价交互 1 / 外部-其他 1 / 价量 1 / 流动性 1。

**内置因子注册（manual run 1024，2026-11-28 14:30）**：

`{"registered": 0, "rejected": 7, "skipped": 0}`

| 表达式 | 闸 | 原因 | 读数 |
|---|---|---|---|
| `log(float_cap)`（size_float） | ② | WEAK_ICIR | ICIR −0.0664，n=499 |
| `ts_mean(turnover,20)`（turnover_20） | ② | WEAK_ICIR | ICIR −0.2151，n=480 |
| 其余 4 条价量 builtin | ② | WEAK_ICIR | 与昨日读数一致 |
| `div(volume,ts_mean(volume,20))` | ④ | REDUNDANT_WITH_POOL | 与昨日一致 |

两条真实口径**已通过闸①字段预检**（不再 `missing_fields`），是在同一五道闸的闸②被拒——即参考数据到位后「两条真基线自动评估」链路按 spec 生效；ICIR 不达标是真实读数，不是数据缺失。**存量 12 条 active 的 id / expression / orientation / pool_increment 与部署前基线逐值相同**（category 仅因上述前置迁移变化）。

**选股 L2 缺口披露（生产快照只读重放，临时 screening 库）**：`status=ok`、`l1_passed=3737`、`l2_top=100`、`selection_mode=factor`、`factor_value_gaps=[]`（当日 12 条参与因子 valid_n 均远超 30 → 无缺口，符合「无缺口为空列表」）。

**读路径附加列**：`read_universe("2026-11-27")` 行携带 `float_cap` / `turnover` / `listed_days`（600519 = 9223）/ `industry_l1`（None，因行业回填未执行）。

## 5. 未完成 / 挂账

1. **行业门类全量回填**：探针 72% 未达 90% 门禁，按 spec 不执行；需换源或改善 cninfo 命中率后重跑（§9.4 已按「探针门禁执行 + 读数」收口，非全量完成）。
2. **周一主表行业增量补抓**：首次自然运行 2026-11-30（周一）15:31；关注 job 时长与成功率（≤50 只/次、失败仅 WARN）。
3. **两条真实口径 builtin**：闸②未过，拒绝池已留痕（次日自动重试）；如需 active 需 ICIR 改善（结构/口径调整另立提案）。
4. **`ind_neutralize` 类因子**：在行业标签覆盖前无法通过闸①（预期行为）。
5. 生产重放 `scripts/replay_factor_harvest.py` 未执行（每条重走 LLM 转译、非确定性；P1 解锁测量已按离线口径收口）。
