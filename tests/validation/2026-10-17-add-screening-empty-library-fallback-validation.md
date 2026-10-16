# add-screening-empty-library-fallback 验证报告

- 日期：2026-10-17（本地门禁）；生产实跑见 §4（部署后补记）
- 变更：`openspec/changes/add-screening-empty-library-fallback`（spec：stock-screening ADDED×1 +
  MODIFIED×2；frontend MODIFIED×1）
- 背景：因子库为空（真实闸口读数 0/5 + 0/4），L2 按契约拒打分 → 选股批次 failed、页面挂死
  （10-15/10-16 两批实测）。本 delta 补「显式披露的过渡通道」：L2 跳过 + 流动性 Top-N + 三处披露，
  闸门/阈值/因子库内容零改动。

## §1 覆盖矩阵（spec 场景 → 证据）

| Spec 场景 | 测试证据 |
|---|---|
| 库空降级产出（批次 ok + fallback + L2 名单/池照常） | `test_empty_library_falls_back_to_liquidity_ranking`（pipeline）；L2 名单=流动性降序、池行 `factor_contribs=[]`、在榜门槛照常累计（pool_size=2 由种子前两日+降级当日凑满 3 日） |
| 三处披露（批次历史/池接口/页面横幅） | `test_record_and_read_back_selection_mode`（store 三处回读）；`test_pool_payload_discloses_fallback_mode`（notice 含「非因子信号」）；`test_batches_endpoint_includes_selection_mode`；前端 `降级批次 → 降级横幅 + 列标题/数值口径切换`；E2E `降级批次 → 披露横幅可见 + 成交额列语义` |
| 恢复自动切回（库有可打分因子 → factor） | `test_factor_mode_regression_and_batch_tag`；`test_pool_payload_factor_mode_has_no_notice`；前端 `factor 批次 → 无降级横幅（回归钉死）` |
| 降级不吞失败（L3 网关整体不可用仍 failed） | `test_fallback_with_l3_gateway_down_still_fails_closed`（批次 failed + 零池） |
| 镜像拒绝条件（IC 缺失边界） | `test_factors_without_ic_records_fall_back`（有 active 因子但无 IC 记录 → 同走降级）；`test_has_scorable_factors_mirrors_ranker_refusal_condition`（含非有限 IC → 交回打分路径 fail loud） |
| 迁移（旧库补列默认 factor） | `test_selection_mode_migration_backfills_legacy_rows` |
| 降级排序确定性/守卫 | `test_liquidity_fallback_rank_*`（降序/tie-break/Top-N 截断/非有限剔除/空输入/守卫，两次调用逐项相等） |

## §2 本地门禁读数（2026-10-17 凌晨）

| 层 | 命令 | 读数 |
|---|---|---|
| 后端相关集 | `uv run pytest tests/screening tests/test_api_screening.py -q` | **174 passed** |
| 后端 lint/类型 | `ruff check` / `ruff format --check` / `mypy src/finance_agent/screening/` | 全干净 |
| 前端类型 | `npx tsc --noEmit` | exit 0 |
| 前端单测（全量） | `npm test`（vitest） | **95 files / 804 passed** |
| E2E 选股页 | `npx playwright test screening-page.spec.ts`（真实后端 + 隔离库；不 mock 业务接口） | **6/6 passed**（含新降级用例） |
| 像素基线 | `npx playwright test --config playwright.ui-baseline.config.ts`（隔离端口对 8006/5179） | **14/14 passed**（screening light/dark 含内）——factor 模式渲染零视觉变更 |
| delta 验证 | `openspec validate add-screening-empty-library-fallback --strict` | valid |

TDD 节奏留痕：各层先红后绿（T1 5 红→26 绿；T2+T3 7 红→68 绿；T4 3 红→30 绿；前端 2 新例先随类型扩展红→9 绿）。

## §3 关键设计落点（实现即设计）

- 触发谓词 `has_scorable_factors` 与打分器守卫共用阈值常量（单一来源，防判定漂移）；
  非有限 IC → 判可打分（交回打分路径 fail loud，降级不吞数据错误）。
- 降级排序 = L1 已算好的近 20 日成交额均值降序（零新增取数）；`composite_score` 语义 = 日均成交额；
  前端 fallback 时列标题与数值口径同步切换（亿元）。
- 披露文案单源在后端 `_SCREENING_FALLBACK_NOTICE`（前端原样渲染）。
- 迁移幂等：`init_screening_db` PRAGMA 检查 + ALTER 补列（旧行默认 `factor`）。

## §4 生产实跑（2026-10-17 凌晨，部署后实测）

- [x] 部署：前端镜像重建（`docker compose up -d --build frontend`）+ 后端容器重启；新代码生效判据 =
      `/api/screening/pool` 载荷出现 `selection_mode`/`notice` 键（部署前无）
- [x] 手动补跑选股日批（ops 通道 `POST /api/v1/ops/jobs/screening_daily/run`，run 108，source=manual）：
      **status=ok / `selection_mode=fallback`**，`l1_passed=3990 → l2_top=100 → l3_keep=100 → pool_size=0`，
      耗时 01:24:31→01:57:01（≈32.5 分钟，瓶颈 = L3 逐候选事件抓取 + 快筛，~百次真实调用）
- [x] 池 0 为**在榜门槛按设计**（今日为降级榜第 1 日；连续 3 日在榜后自动出池，预期 10-20）
- [x] 页面人工核验（真实浏览器截图 `D:/tmp/screening-fallback-verify.png`）：降级披露横幅逐字显示
      「因子库无可打分因子：本次为降级模式，按近 20 日日均成交额排序（非因子信号）」；批次日期
      2026-10-16；**无失败横幅**（批次 ok）；空态文案 = 通用「等待下一批」——轻微文案不精确
      （空因=在榜门槛第 1 日而非缺批次），记 Minor 待后续微调
- 失败横幅回归：部署前旧失败批次行为不变（`stale` 逻辑未动）

## §5 备注

- 生产库 `data/screening.db` 的旧批次行经迁移自动补 `selection_mode='factor'`（语义：历史批次为
  因子路径尝试/未判定）。
- 降级为过渡通道：因子库出现可打分因子后同批日自动回到 `factor` 路径，无需人工切换。
- Minor（后续微调）：降级首日的空态文案可细化（「在榜门槛累计中」而非「等待下一批」）。
