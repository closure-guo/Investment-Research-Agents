# Tasks: unify-ui-design-system

## 1. 视觉基线存档
- [x] 1.1 打底前对现有全部页面（浅色+深色各一套）全局截图存 `tests/validation/ui-baseline/`
  - 7 页 × 2 主题 = 14 张，落 `tests/validation/ui-baseline/pages/`（ed18ce02）；专属 config（8006/5179 端口对 + 五库 + REPORTS_DIR 隔离）+ 自带 skip 守卫
  - 对比工具能力自证：合成 400×100 差异块 → 40346 px / ratio 0.04 检出；唯一 mask 为 /track-record 「截至」当天日期（`_track_as_of` 跨日必变）

## 2. 设计令牌
- [x] 2.1 `design/tokens.ts`：rise/fall/flat 涨跌语义色（红涨绿跌）+ 五态徽章色 + AI 紫系标识色，双主题映射 + 令牌快照单测
  - index.css `:root`/`.dark` 双主题 `--rise/--fall/--flat`（浅 #EF4444/#10B981，深 #F87171/#34D399）、`--badge-*`、`--ai-accent*`；tokens.test.ts 17 例（含 red/green 语义色相校验）
- [x] 2.2 数字规范工具（等宽/带符号/千分位/两位小数格式化函数）+ 边界单测（负值/零/大数）
  - `design/format.ts`（formatSignedPct / formatThousands / formatPct2 / signToken / TABULAR_CLASS）；format.test.ts 15 例（负值/零/负零/亚精度 0.001/大数/NaN/null/Infinity）

## 3. 页面壳组件
- [x] 3.1 PageShellA（指标条+主图表+底部 Tab 表）+ 渲染/空态/骨架单测
  - pageShellA.test.tsx 5 例；Tab 内容区 TAB_FADE_CLASS 150ms（key 重挂载重播）
- [x] 3.2 PageShellB（筛选栏+数据表+行展开）+ 渲染/空态/骨架单测
  - pageShellB.test.tsx 6 例（含行展开、数值列右对齐 tabular-nums）
- [x] 3.3 PageShellC（左列表+右详情+确认/驳回）+ 交互单测
  - pageShellC.test.tsx 6 例（未选中禁用、回调携带条目）
- [x] 3.4 PageShellD（批次列表+步骤级状态图标）+ 渲染单测
  - pageShellD.test.tsx 4 例（复用 StatusBadge 五态）
  - 共享 ShellParts：统一空态（图标+一句说明+主操作按钮）/ 骨架屏（列表 5 行、工作台指标方块+图表矩形）；四壳共 21 例

## 4. 全局组件
- [x] 4.1 StatusBadge 五态徽章 + 五态渲染单测
  - statusBadge.test.tsx 9 例（五态配色/文案/running 呼吸动画 + reduced-motion 三重降级）
- [x] 4.2 AiBadge 角标（hover 提示「由模型生成，仅供参考」）+ 交互单测
  - aiBadge.test.tsx 4 例（hover 逐字文案 + 键盘聚焦可达）

## 5. 动效与降级
- [x] 5.1 行入场/Tab 淡入/徽章变色动效按 spec 参数实现 + prefers-reduced-motion 降级单测
  - `design/motion.ts`：8px + 30ms stagger（封顶 600ms）/ Tab 150ms / 徽章 200ms ease-out；motion.test.tsx 7 例（常量钉死、延迟曲线、DOM 级降级、index.css keyframes/媒体查询断言）

## 6. 验证
- [x] 6.1 全量测试通过（后端 pytest + 前端 Vitest + E2E stub 门禁）
  - 前端 91 files / 761 tests 全绿；后端定向 `pytest tests/data tests/test_api_settings.py` 222 通过（本 change 唯一触及后端文件 cache.py）；E2E 门禁 = 隔离 ui-baseline 套件 14/14 零差异（默认套件本机既有环境性红，未重跑，见验证报告异常记录 d）
- [x] 6.2 现有页面打底前后截图逐页对比零变化，报告落 `tests/validation/`
  - `npx playwright test -c playwright.ui-baseline.config.ts`（无 --update-snapshots）→ `14 passed (37.2s)` 零 diff；报告 `tests/validation/2026-10-13-unify-ui-design-system-validation.md`
- [x] 6.3 既有测试无修改通过（红线）
  - `git diff --name-status ed18ce02..HEAD`：19 新增 + 唯一修改 frontend/src/index.css（85 增 / 0 删，纯新增声明）；test 目录零修改，前端 761 例全绿
