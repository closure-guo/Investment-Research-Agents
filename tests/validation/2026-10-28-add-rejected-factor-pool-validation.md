# add-rejected-factor-pool 验证报告（2026-10-28）

**delta**: `openspec/changes/add-rejected-factor-pool/`（PR #504，squash=3728402d）
**范围**: delta tasks.md 5.1–5.2（合并部署 + 回填 + API 泄漏验证）；5.3 的次日监控读数核对待 10-28 17:55 日批后补录。

## 合并与部署

- PR #504 合并：lint-and-test 绿；stub 首跑红（daily-ops 时间线徽章"今日漏跑"超枚举，issue #503 时钟窗口 flake 家族——main 同窗 00:35 UTC CI failure 同族），rerun 后 14m26s 绿，判定 flake 非回归。
- 主检出同步：`merge-tree` 预演 0 冲突 → 未跟踪的 fix-stability-gate-mode-axis 陈旧副本（与 origin/main 版本内容有差异）整体备份至仓库外 `/d/WorkSpace/_backup/rejected-factor-pool-20261028/` 后删除 → stash → `pull --ff-only` → pop。
- 部署：`docker compose up -d --build backend`（09:14，红线检查通过：running_sessions=0 / running_jobs=0 / 非日批窗口）；容器内三处新代码确认（`record_rejected_factor` / `rejected_checked` / `reject_backfill.py`）。

## 存量回填（tasks 5.3 第一半，已完成）

- 备份：`data/factor_library.db.bak-pre-rejected-backfill-20261028-0915`（221,184 B）。
- 命令：`uv run python scripts/backfill_rejected_factors.py --state data/factor_research/harvest_state.json --db data/factor_library.db --candidates data/factor_research/candidates.json`
- 读数：

```json
{"scanned": 1975, "imported": 182, "updated": 352, "skipped_no_expression": 0, "skipped_unparseable": 0, "skipped_other": 1441}
```

- **对账**：rejected 记录 534 = imported 182（新建行）+ updated 352（同构 upsert 合并，RD-Agent 重提重复）；skipped_other 1441 = untranslatable 1411 + transcription_error 18 + admitted 12。零偏差。
- **拒绝池闸口分布**（182 行）：闸② 132（WEAK_ICIR）｜闸④ 36（池增量/REDUNDANT 族，本 change 的主要复评价值所在）｜闸⑤ 6｜闸0 5｜闸① 3。抽样事件形态正确（`created` + `gate`/`reason`/判定读数）。

## API 泄漏验证

`GET /api/factors`（部署后容器，挂载同一 `data/`）：返回 12 条（active 10 + retired 2），`rejected` 泄漏 **0**——`list_factors` 缺省排除在线上生效。详情接口按 id 直查拒绝池行可用（只读诊断）。

## 待补录（5.3 第二半）

- [x] 10-28 17:55 窗口 `factor_decay_monitor` 日批后：`GET /api/v1/ops/jobs` 核对摘要含 `rejected_checked=182` 且 `rejected_days_recorded>0`；补录到本报告。
- [x] 补录完成后：openspec sync + archive（tasks.md 5.3 勾选）。

### 补录读数（2026-10-28 18:32 核对，定时任务）

`factor_decay_monitor` run 330（工作日 15:45 排程，15:45:00 → 16:04:32，status=ok；实际排程为 15:45 而非报告早前所写 17:55——17:55 属收割/cohort 族，此处更正）：

| 字段 | 读数 | 判定 |
|---|---|---|
| `rejected_checked` | 182 | ✓ 与核对时刻拒绝池行数精确一致 |
| `rejected_recorded` | 181 | ✓（1 行无可算 IC，计入跳过语义） |
| `rejected_days_recorded` | 13835 | ✓ 缺口回填一次补齐面板窗口（≈182 行 × ~76 交易日） |

补充闭环证据：监控运行后 15:55 收割日批产生的新拒收**自动落池**（核对时池 182 → 191 行，+9），收割导入路径的拒收持久化无需人工干预即生效。归档顺延条件未触发，tasks 5.3 全勾，sync + archive 随本次提交。

## 回滚位

- 回填唯一写入面 = `factor_library.db` 的 factors/factor_events 两表（182 行 + 对应事件）；库备份见上。回滚 = 恢复备份文件（监控日批此前从未写过 rejected IC，无其他面）。
- 代码回滚 = revert 3728402d + 重建镜像（`list_factors` 对旧代码语义向后兼容：rejected 行只是多出来的普通行，会被旧代码当普通因子返回——回滚代码前须先还原库）。
