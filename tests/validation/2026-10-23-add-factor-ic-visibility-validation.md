# 人工验证报告: add-factor-ic-visibility

**日期**: 2026-10-23
**验证人**: Agent 自动验证（人工抽查待补）
**关联 delta**: `openspec/changes/add-factor-ic-visibility/`
**E2E 门禁**: `tests/e2e/playwright/test-results/`（isolated config，端口 8001/5174）

## 自动化验证结果

| 项 | 命令 | 结果 |
|---|---|---|
| 后端就绪度纯函数 | `pytest tests/factors/test_readiness.py -q` | 8 passed |
| 后端接口字段 | `pytest tests/test_api_factors.py tests/test_api_screening.py -q` | 57 passed（含新增 3 例） |
| 前端类型检查 | `npx tsc --noEmit` | 0 error |
| 前端单测 | `npm test -- --run` | 913 passed / 102 files |
| E2E 因子库页 | `npx playwright test factor-library.spec.ts --config=playwright.isolated.config.ts` | 4 passed |
| E2E 选股页 | `npx playwright test screening-page.spec.ts --config=playwright.isolated.config.ts` | 7 passed（含「因子信号状态卡」新例） |
| 线上接口实测 | `GET /api/factors/1` | 200，0.25s，`ic_points=4`、`ic_window=60`、`min_samples_for_scoring=10`、`expected_factor_mode_date=2026-11-02` |
| 线上池接口实测 | `GET /api/screening/pool` | 200，`factor_signal.factors` 10 项，最早恢复日 `2026-11-02`，`reason="IC 样本不足（门槛 10）"` |

## 人工验证（待用户确认）

线上实测截图（Chromium，1440×900，真实前后端）：

- 因子库页展开：`tmp/factors-ic.png` — IC 折线（3 点：10-19/20/21）、「样本 3/60」「打分门槛 3/10」「预计 2026-11-03 恢复因子打分（按工作日推算、不含节假日）」、黄色「样本不足（3/10）」、列表 ICIR 旁「样本 3」。
- 选股页：`tmp/screening-signal.png` — 「因子信号状态」卡逐因子 4/10、3/10、1/10，未达标与逐因子预计日，底部「预计 2026-11-02 恢复因子打分」。

| 场景 | 预期 | 实际 | 通过 |
|---|---|---|---|
| `/factors` 展开因子详情 | 出现 IC 折线图；显示「样本 3/60」「打分门槛 3/10」「预计 2026-11-03 恢复因子打分（按工作日推算）」；样本不足提示 | 截图实测符合 | ☑ |
| `/factors` 列表 ICIR 列 | ICIR 数值旁显示「样本 n」；无 IC 记录仍显示「—」 | 截图实测符合 | ☑ |
| `/screening` 因子信号卡 | 显示 10 个 active 因子的 `n/10`、未达标/已达标、预计恢复日；降级原因逐字 | 截图实测符合 | ☑ |
| 详情页响应速度 | 展开不再转圈超时（修复前 >110s） | 实测 0.25s | ☑ |

## 异常记录

1. **详情接口曾整体超时**：`_factor_style_exposure_payload` 无条件构建 500 日 × 全市场面板（实测数分钟），所有因子详情「加载失败」。已加短路（IC 点数 < 30 直接 `null`），本次一并纳入规格约束。
2. **快照库 WAL 侧车文件**：排查期间从宿主机只读查询 `market_snapshot.db` 生成 `-shm/-wal`，容器侧 `PRAGMA journal_mode=WAL` 报 I/O 错误；停后端删除 0 字节 WAL 与 shm 后恢复（incident 046 同族）。后续快照库只在容器内读。
3. **全量后端套件**：`pytest -q` 全量运行 26 分钟未结束（网络类用例拖时）后中止；改跑改动相关子集 `tests/factors tests/screening tests/test_api_factors.py tests/test_api_screening.py`：**686 passed / 1 failed**。唯一失败 `tests/screening/test_report_trigger.py::TestCapsAndIdempotency::test_per_report_cap` 与本变更无关（走真实 WebSearch，环境 TLS/网络失败导致候选被过滤，单测单独复跑同样失败），属既有环境性失败。

## 结论

- [x] 自动化门禁通过（单测 / 类型检查 / E2E）
- [ ] 人工抽查通过（待用户确认上方三行）
- [ ] 可 archive
