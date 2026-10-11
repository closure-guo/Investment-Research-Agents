# 人工验证报告：fix-earnings-marker-caliber（issue #243 子项3）

日期：2026-10-10 ｜ 验证人：agent（浏览器实测 + 单测双轨）｜ 环境：worktree `.worktrees/earnings-caliber`，stub 后端（TESTING=1，端口 8012，隔离 SESSIONS_DB_PATH）+ vite dev（5199，VITE_API_TARGET 指向 stub）

## 验证项与结果

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | 热力图卡片标题「年报报告期窗口股价变化（%）」 | stub 会话（601818）→ 报告页 DOM 断言 + 截图 | ✅ 新标题在线，见 `-heatmap-card.png` |
| 2 | 折线卡片标题「股价趋势（红色虚线为年报报告期截止日）」 | 同会话 chart_data 剥离 OHLC（sqlite 直改测试库）→ 折线降级分支 DOM 断言 + 截图 | ✅ 新标题在线，见 `-line-card.png` |
| 3 | 「发布日/发布窗口」措辞清零 | 页面 innerText + TreeWalker 全量扫描 | ✅ 0 残留（修复前实测命中「财报发布窗口期股价变化」） |
| 4 | 报告正文热力图题注「年报报告期窗口股价变化」 | report.py `all_chart_titles` 改动经 stub 管线实跑，DOM 复验 | ✅ |
| 5 | 服务端 PNG 竖线注记「报告期止」 | 直接渲染证据 PNG（earnings 日期与日线相交） | ✅ 见 `-png.png`（2025-12-31 红色虚线 + 红色「报告期止」） |
| 6 | K 线分支卡片标题无「发布日」措辞 | DOM 快照（「股价 K 线（MA5/20/60…）」无发布措辞，本就无） | ✅ |

## 自动化门禁

- 后端：`pytest tests/test_charts_earnings_label.py` 3/3 绿（红→绿：annotate 文案断言先红）；charts 相关 4 文件 33 例绿
- 前端：`vitest run earningsMarkerCaliber / stockPriceKline / chartsMarkLine` 12/12 绿（两 wording 用例先红）
- ruff 净

## 如实注记

- §5.6 Playwright e2e 基建未建设（#162 待裁决）：本 delta 以前端 vitest 契约 + 人工浏览器验证替代 E2E 门禁，不静默勾选
- 数据口径未改：earnings_dates 仍是利润表报告日（报告期截止日），仅措辞如实化；接真实披露日历数据源留作后续（东财接口本机 IP 受限暂不可验证）
