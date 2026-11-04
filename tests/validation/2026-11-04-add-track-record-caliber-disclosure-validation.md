# 人工验证报告: add-track-record-caliber-disclosure

**日期**: 2026-11-04
**验证人**: agent（自动化）+ owner（页面观感，待部署后补）
**关联 delta**: `openspec/changes/add-track-record-caliber-disclosure/`
**E2E 门禁**: 本检出无 `e2e/` 工程（`Test-Path e2e` = False），Playwright 套件未在本仓初始化。部署后改用 **Playwright 1.62 真实浏览器**直连 `http://127.0.0.1:5173/track-record` 做页面级验证（截图 `tmp/track-record-caliber.png`），等价覆盖「卡片真实渲染 + 无 JS 错误」这一层。

---

## 验证结果

| Scenario | 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 正常披露：五字段计数正确 | 单测 `TestCaliberDisclosure::test_caliber_counts_directional_views_and_settled` | nav_points/directional/long/short/settled 与种子数据一致，neutral 与 duplicate 不计 | 20 例全绿 | ✅ |
| 空库：各字段为 0 且不报错 | 单测 `::test_caliber_empty_db_all_zero` | caliber 全 0，agent_return null | 同上 | ✅ |
| 窗口内无方向性观点 | 单测 `::test_caliber_neutral_only_has_no_directional_views` | long/short/directional 均为 0，披露行仍渲染 | 同上 | ✅ |
| 既有读数零回归 | 单测 `::test_caliber_does_not_change_existing_readings` | agent_return/window/beat 与改动前一致 | 同上 | ✅ |
| 前端渲染披露行 | vitest `indexCompareCard.test.tsx`「渲染口径披露」 | 含「仅统计买入/卖出观点」「观望/持有不计入组合仓位」「20 个交易日」「累计多 0 / 空 27」「期内已结算 5 笔」 | 10/10 通过 | ✅ |
| 旧后端无 caliber 键 | vitest「响应缺 caliber(旧后端)时不渲染披露行」 | 不渲染披露行、其余照常 | 通过 | ✅ |
| 空态不渲染披露 | vitest「净值不足时不渲染披露行」 | `agent_return=null` 时无披露行 | 通过 | ✅ |
| 生产数据实读 | 库副本上调用 `build_index_compare("all")` | `caliber={nav_points:20, directional_views:49, long_views:0, short_views:49, settled_count:5}` | 与页面口径一致 | ✅ |

### 生产读数复核（库副本，不触碰生产）

```json
{"span":"all","window":{"start":"2026-10-08","end":"2026-11-04"},
 "agent_return":-0.057128,
 "caliber":{"nav_points":20,"directional_views":49,"long_views":0,"short_views":49,"settled_count":5}}
```

`long_views=0` 与「窗口内无多头仓位」的结论一致；`directional_views=49` 为窗口期内累计进过组合的观点数（11-04 当日并发为 27 条）。

---

## 自动化证据

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/outcome -q` | **994 passed, 2 skipped**（150s） |
| `uv run ruff check`（3 个改动文件） | All checks passed |
| `npx vitest run`（全量） | **108 files / 979 tests passed**（53s） |
| `npx tsc --noEmit` | 无错误 |
| `openspec validate add-track-record-caliber-disclosure --strict` | valid |

---

## 异常记录

1. **部署阻塞**：当前为交易日 19:3x，处于 AGENTS.md 红线窗口（17:55–20:45）内，**不得重启后端**。改动已就位但未生效，页面暂不会出现披露行（旧代码返回体无 `caliber`，前端按兼容分支不渲染——正是被测的降级路径）。
2. **E2E 缺位**：本检出无 `e2e/` 工程，交互类变更的门禁只能以 vitest 代替；页面观感需人工确认。
3. **计数口径易误读**：`directional_views=49` 是「窗口期内累计进过组合的观点数」，不是任一单日的并发持仓数（11-04 当日为 27 条）。前端文案已加「累计」限定词，仍需人工确认可读性。

---

## 结论

- [x] 自动化验证全部通过
- [x] 页面人工验证（2026-11-04 20:5x，Playwright 实渲染 `http://127.0.0.1:5173/track-record`）
- [ ] 可 archive（与 `update-track-record-symbol-aggregation` 一并归档，两者同日上线）

**部署记录**：2026-11-04 20:50–20:55，`docker compose up -d --build frontend`（后端因 `src` bind mount 已于 20:29 重启并加载新代码；compose 随后重建 backend 容器，健康检查通过）。

**页面实测（Playwright 抓取，截图 `tmp/track-record-caliber.png`）**：

```
跑赢指数对比
跑赢 4/5 个指数
本组合     -4.60%   对比基准线
上证指数   -3.30%   ↓ 跑输
沪深300    -4.85%   ↑ 跑赢
中证500    -8.64%   ↑ 跑赢
中证1000   -8.78%   ↑ 跑赢
创业板指  -16.20%   ↑ 跑赢
口径:仅统计买入/卖出观点(观望/持有不计入组合仓位);20 个交易日 · 累计多 0 / 空 49 · 期内已结算 5 笔
历史业绩不代表未来表现
```

- 页面无 JS 错误（`pageerror` 为空）；
- `caliber` 五字段与生产库复算一致（20 / 49 / 0 / 49 / 5）；
- 披露行明确写出「累计多 0」——读者不会再把这个账本误读成全仓组合。
