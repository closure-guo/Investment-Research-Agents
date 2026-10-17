# 人工验证报告: fix-charts-frontend-units-336

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（自动化浏览器驱动 + 截图目检；owner 终审待补）
**关联 issue**: #336（fix(charts): 报告财务图表区单位错配）
**规范依据**: `openspec/specs/chart-data-integrity/spec.md` —— 「图表金额单位一致」「年度图表时间轴升序呈现」「图表缺数据禁止填 0 渲染」（三条 requirement 此前仅后端 matplotlib 路径落实，前端 ECharts 层为违背态）
**E2E 门禁**: 本次为渲染层缺陷修复（前端单测契约 + 真实栈浏览器验证；不新增 E2E spec——default 套件的 stub 金额为小数值（1000.0 量级），无法承载真实量纲断言，改动 stub 会连带大量存量报告文本断言，按 issue「可选」项处理并如实披露）
**执行环境**: 主检出 `D:\WorkSpace\finance_analysis_agent` @ `fix/charts-frontend-units-order`；前端 dev 容器（vite dev，5173）+ 生产后端（8000，331 会话零 running）；样本会话 = 光大银行 601818 `4a76bc58-d8e`（10-17 03:45，真实 LLM 跑批产物）
**证据**: `tests/validation/2026-10-17-fix-charts-frontend-units-fullpage.png`（整页）、`...-kpi.png`（图表区首屏）

## 验证结果

| # | Scenario（主规范） | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 图表金额单位一致（现金流 Scenario：162907000000.0 元 → 1629.07） | 亿元轴标序列以亿元渲染；MUST NOT 原始元配「亿元」标 | 单测 `chartsDataIntegrity.test.tsx`（spec 数值逐字）；浏览器实拍：经营现金流图轴刻度 -3,000~2,000 亿元、2025 柱 ≈1629；营收/利润/总资产/权益/合同负债同；KPI 卡「2025 营收 1263.1亿 / 净利润 388.3亿 / 总市值 1832亿」，溢出消除 | ✅ |
| 2 | 年度图表时间轴升序呈现 | 数据降序（index 0 = 最新）→ 呈现层反转，最旧在左 | 浏览器实拍八图横轴均 2021→2025 升序；单测钉横轴数据与序列顺序 | ✅ |
| 3 | 图表缺数据禁止填 0 渲染 | 柱图缺失年断柱（NaN），MUST NOT 0 高度柱 | 单测：净利/现金流缺失年 `Number.isNaN === true`；换算 helper 缺失 → NaN 与后端 `_yi_series` 同语义 | ✅ |
| 4 | 顺带发现：横轴年份标签此前整体缺失 | 年度图表以年份为横轴 | 修复前 option 未设 `xAxis.data`（`years` 变量计算后未使用）——本次随单位修复一并补齐（八图 + 增速vs股价），实拍横轴年份可见 | ✅ |

## 修复清单（frontend/src/Charts.tsx）

- 新增 `ascending()` / `yiSeries()` 两个呈现层 helper（头注释对齐后端 `charts.py` 的 `_ascending` / `_yi_series` 语义）；
- 六处金额序列 `/1e8` + 缺失 → NaN：营收、归母净利润、经营现金流净额、总资产、归母权益、合同负债；KPI 卡营收/净利润同步换算；
- 九图横轴升序（数据不 reverse，仅呈现层）+ 补齐 `xAxis.data` 年份标签；热力图年行升序（y 坐标重映射）；增速 vs 股价图三序列同向反转。

## 回归

- 前端全量：`npx vitest run` → **97 文件 / 826 例全绿**（含新增 11 例）；`npx tsc --noEmit` 零错误；
- 真实栈浏览器（playwright manual config 对 5173/8000）：KPI 断言 `1263.1亿`/`388.3亿` 可见、图表区无原始元文本、营收图 canvas 挂载——通过后临时 spec 已删（不入默认套件）。

## 未达成项 / 披露

1. **default E2E 套件未含单位断言**（stub 金额小数值，见「E2E 门禁」行）；真实量纲断言由前端 vitest 契约测试钉死（CI 门禁运行）；如后续升级 stub 金额量纲，应同步补 E2E 断言。
2. 修复前报告的**存量会话**（数据库内已渲染产物）无影响——chart_data 载荷本身未变（数据层「统一元」契约不变），换算全在呈现层，刷新即正确。
3. 升序/轴标两项为 #336 修复过程中发现的**同族规范违背**（同文件同 delta 家族，后端早已合规），非 issue 原文列举项——随本修复一并收口，PR 中如实注明。
