# unify-ui-design-system 人工验证报告

> 状态：**待人工验证**（机器验证通过；签字栏留空待用户确认）
> 日期：2026-10-13 ｜ 分支 `feat/screening-stack` ｜ 证据采集时 HEAD：`7495ea0a`

## 概述

OpenSpec change `unify-ui-design-system`（打底阶段）：新增涨跌语义令牌（A 股红涨绿跌）、五态状态徽章、AI 生成内容角标、四种页面壳（A/B/C/D）与动效规范。本 change **只新增令牌/组件/壳，不改任何现有页面的视觉与行为**（spec「本 change 零视觉变更红线」）。

改动提交（T1~T5，本 worktree）：

| 提交 | 内容 |
|---|---|
| ed18ce02 | T1 打底前全页面视觉基线（14 张 PNG + 专属 config/spec + `CACHE_DB_PATH` 支撑改动） |
| dc380930 | T2 设计令牌 + 数字规范 |
| 83359fa3 | T5 动效规范 + reduced-motion 降级 |
| c52998bd | T3/T4 StatusBadge + AiBadge |
| 7495ea0a | T4 四种页面壳 PageShellA~D（空态/骨架屏） |

## 验证结果表

| # | 验证项 | 验收标准（spec Scenario） | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 涨跌语义令牌（红涨绿跌双主题） | +2.31% 用 rise 红、-1.05% 用 fall 绿；深色主题语义不变；页面禁写红/绿裸色值 | ✅ | `frontend/src/design/tokens.ts` + index.css `:root`/`.dark` 双主题 `--rise/--fall/--flat`；`tokens.test.ts` 17 例（双主题取值解析 + red/green 语义色相校验）；`format.test.ts` 15 例（+2.31%/-1.05%/0.00%、负零、亚精度、NaN/null/Infinity/大数、`signToken` 着色） |
| 2 | 五态徽章（含 running 呼吸动画与 reduced-motion 降级） | active 实心绿底 / degraded 琥珀描边 / retired 灰底 / running 蓝底+呼吸动画 / failed 红底；reduced-motion 时动画禁用 | ✅ | `frontend/src/components/StatusBadge.tsx`（枚举唯一来源 `design/tokens.ts`）；`statusBadge.test.tsx` 9 例（五态渲染/配色令牌/文案/动画类有无/reduced-motion 降级）；index.css `@keyframes badge-breath`（1.6s 循环）+ `@media (prefers-reduced-motion: reduce)` 禁用块 |
| 3 | AI 角标（hover 文案逐字） | LLM 生成内容带紫系 AiBadge；hover 提示「由模型生成，仅供参考」；确定性数字无角标 | ✅ | `frontend/src/components/AiBadge.tsx`（`AI_BADGE_HINT='由模型生成，仅供参考'` 唯一文案来源，shadcn Tooltip hover/聚焦）；`aiBadge.test.tsx` 4 例（紫系令牌/aria-label、hover 逐字文案、键盘聚焦可达、小号角标默认文案） |
| 4 | 四种页面壳（含空态与骨架屏） | A 指标条+主图表+底部 Tab 表；B 筛选栏+数据表+行展开；C 左列表+右详情+确认/驳回；D 批次列表+步骤级状态图标；空态=图标+一句说明+主操作按钮；加载=5 行骨架 | ✅ | `frontend/src/components/shells/PageShellA~D.tsx` + `ShellParts.tsx`；`pageShellA/B/C/D.test.tsx` 共 21 例（渲染/空态/骨架、C 确认驳回禁用态、B 行展开、数值列右对齐 tabular-nums） |
| 5 | 动效规范（行入场 8px+30ms stagger、Tab 150ms、徽章 200ms ease-out、reduced-motion 全禁） | 参数写死且降级无动画 | ✅ | `frontend/src/design/motion.ts`（`ROW_ENTRANCE_Y=8` / `STAGGER_MS=30` 封顶 600ms / `TAB_FADE_MS=150` / `BADGE_TRANSITION_MS=200`）；`motion.test.tsx` 7 例（常量钉死、stagger 延迟曲线、DOM 级 reduced 降级、index.css keyframes 与媒体查询断言） |
| 6 | **零视觉变更红线（14/14 页面×主题像素对比 ≤0.1%）** | 打底前后现有全部页面（浅+深）逐页对比，容差 ≤0.1% 像素（`maxDiffPixelRatio: 0.001`） | ✅ | `npx playwright test -c playwright.ui-baseline.config.ts`（**无** `--update-snapshots`）→ **`14 passed (37.2s)`**，0 例上报差异（≤0.1% 容差，maxDiffPixelRatio 0.001）；运行后 `git status` 干净（基线 PNG 未被重写） |

设计层新增单测合计：9 文件 / 73 例，全绿（含入前端全量 91 files / 761 tests）。

## 零视觉变更红线证据（本 change 核心验收）

命令（在 `tests/e2e/playwright/` 下）：

```bash
npx playwright test -c playwright.ui-baseline.config.ts
```

结果行：**`14 passed (37.2s)`**（≤0.1% 容差下 0 例上报差异；2026-10-13 18:35 完成；HEAD `7495ea0a`；无 `--update-snapshots`，纯像素对比）。

环境隔离与确定性（T1 已固化，本次沿用）：专属端口对 8006/5179；五个测试库 `data/test-e2e-ui-baseline*.db`（含 `CACHE_DB_PATH`）；`REPORTS_DIR` 专属空目录；固定视口 1440×900 且 `colorScheme`/`timezoneId` 钉死；`fa_theme` 预置双主题。

### 基线矩阵（7 页 × 2 主题 = 14 张，`tests/validation/ui-baseline/pages/`）

| 页面 | URL | 浅色 | 深色 |
|---|---|---|---|
| 首页空态 | `/` | `home-light.png` | `home-dark.png` |
| 下载中心空态 | `/downloads` | `downloads-light.png` | `downloads-dark.png` |
| 战绩页空态 | `/track-record` | `track-record-light.png` | `track-record-dark.png` |
| 设置·LLM 配置 | `/settings` | `settings-llm-light.png` | `settings-llm-dark.png` |
| 设置·缓存管理 | `/settings`（切缓存分区） | `settings-cache-light.png` | `settings-cache-dark.png` |
| 设置·数据监控 | `/settings`（切监控分区） | `settings-monitor-light.png` | `settings-monitor-dark.png` |
| 选股池空态 | `/screening` | `screening-light.png` | `screening-dark.png` |

未采集页面（T1 spec 文件头已文档化，非静默丢弃）：`/track-record/calibration`、`/track-record/predictions/{id}`——打底前无预测数据时均为纯空态或依赖 prediction_id 的详情态；后续 delta 触达时应补采。

### 对比工具能力自证（T1 记录，提交 ed18ce02）

- 向 `home-light.png` 合成注入 400×100 差异块后重跑该单例：Playwright 报 **40346 像素差异 / ratio 0.04**。计数构成精确为：注入块 400×100 = 40000 px + 块外 ≤346 px 容差内页面噪声（40346 − 40000）——该噪声平日低于容差不上报，注入块才使其一并计入；相对 1440×900 画布 raw 占比 ≈3.1%（Playwright 的 ratio 为向上取整到两位小数 → 0.04）。40346 px 远超 `maxDiffPixelRatio: 0.001`（1440×900 下允许 1296 px）→ 必红，证明对比链路真在逐像素比对而非空跑；还原基线后重跑 14/14（0 例上报差异）。
- 该合成自证为一次性运行，未提交产物/失败日志（`tests/validation/ui-baseline/` 下仅存基线 PNG）；上述数字即其留存记录。
- 真实回归发生时 actual/diff PNG 落 `tests/e2e/playwright/test-results/` 供人工核验。

### 唯一 mask（文档化，非跳过页面）

- `/track-record` 总览「截至」卡的裸日期文本（`getByTestId('track-record-summary').getByText(/^\d{4}-\d{2}-\d{2}$/)`）：该值来自后端 `_track_as_of`（北京时间当天），跨日必变；基线与对比两次运行同样遮蔽。该页其余全部像素参与对比。
- 无其他 mask：专属 cache 库为空 → 缓存/监控分区的计数、时间戳均为确定值（0 / — / 空表文案）。

## 后端定向回归（支撑性改动）

```bash
uv run pytest tests/data tests/test_api_settings.py -q
```

结果：**`222 passed, 1 warning in 48.53s`**（唯一 warning 为 FastAPI/Starlette httpx 弃用提示，既有）。

口径说明：本 change 唯一触碰的后端文件是 `src/finance_agent/data/cache.py`（见异常记录 a），`tests/data` 全量 + `tests/test_api_settings.py` 即该路径的定向回归；后端全量套件（含 evals/live 等）超出变更影响面，未在本轮重跑。

## 前端全量回归

```bash
cd frontend && npx vitest run
```

结果：**`Test Files 91 passed (91)` / `Tests 761 passed (761)`**（52.29s）。

既有测试零修改证据：`git diff --name-status ed18ce02^..HEAD`（本 change 全范围提交，含 T1 基线提交 ed18ce02）共 41 个新增文件 + 3 个修改文件——`.gitignore`（+3/−0，新增 ui-baseline 测试库忽略项）、`frontend/src/index.css`（+85/−0，纯新增声明）、`src/finance_agent/data/cache.py`（+6/−1，调用期读 `CACHE_DB_PATH`，默认值不变，见异常记录 a）；3 个修改文件均非测试文件，`frontend/src/test/` 与 `tests/` 下无任何既有测试文件被修改。

## 异常记录

**(a) 支撑性后端改动 `CACHE_DB_PATH`（超出 proposal 声明的「前端」影响面，如实记录）**

- T1 在 `src/finance_agent/data/cache.py` 的 `get_shared_cache()` 增加调用期 `os.environ.get("CACHE_DB_PATH", "cache.db")` 读取（默认值与既有行为完全一致，同 `SESSIONS_DB_PATH` 等 *_DB_PATH 纪律）。
- 必要性（基线确定性）：首轮采集未隔离时，设置中心「缓存管理」「数据监控」两分区把外部进程写入的 **17 条 cache.db 条目**入镜，且随外部写入与跨日过期漂移 → 截图不可复现。隔离后为空库确定值。
- 这是本 change 唯一触碰的后端文件；由 `tests/data` + `tests/test_api_settings.py` 定向覆盖（222 通过）。

**(b) `@tanstack/react-table` 不是本仓库依赖 → 壳用原生 `<table>`（未新增依赖）**

- proposal 的表格约定提及 TanStack Table，但 `frontend/package.json` 实际不存在该依赖（亦无其他 @tanstack 包）。
- 按 proposal 非目标「不引入新 UI 依赖」，PageShellB 用原生 `<table>` 实现，数值列右对齐 + `tabular-nums font-mono`（`TABULAR_CLASS`）等数值规范照常生效。是否需要在后续 delta 引入表格库，留待人工评审。

**(c) 无独立 `SignedPct` 渲染组件（functions + 壳覆盖数字规范场景）**

- spec「涨跌色渲染」场景的表述是「以数字规范组件渲染」，实际实现为 `formatSignedPct()` + `signToken()` 函数（`design/format.ts`、`design/tokens.ts`）+ 页面壳内的数值列渲染，未单独封装 `<SignedPct>` 组件。
- 该场景的验收语义（+2.31% 红 / -1.05% 绿 / 深色不变）已由 format/tokens 单测与壳单测覆盖；是否补组件封装请人工评审决定。

**(d) 默认 E2E 套件本机既有环境性红（非本 change 引入；本 change 门禁为隔离 ui-baseline 套件）**

- 本机默认 `npx playwright test` 套件存在既有环境性失败（Windows 并发 worker + vite polling 等，见 `tests/validation/2026-08-29-refactor-ui-design-system-validation.md` 的 E2E 失败归因；vite 代理默认 8000 + `reuseExistingServer` 复用生产容器的 footgun 见 `docs/incidents/038-e2e-default-suite-hits-production-vite-proxy-20261007.md`）。
- 本轮**未重跑**默认套件；本 change 的 E2E 门禁是隔离 `playwright.ui-baseline.config.ts` 套件（14/14 在 ≤0.1% 容差内 0 例上报差异），其自带守卫保证被默认 config 扫到时整组 skip。

**(e) 两个 spec Scenario 的真实页面落地由后续功能页 delta 承接（本 change 仅组件级覆盖）**

- spec「角标展示」Scenario（GIVEN 选股页一行候选：L3 理由带 AiBadge、综合分数字无角标）与「状态展示复用 StatusBadge」（即「状态徽章五态」Requirement 的「任何页面的状态展示 SHALL 复用该组件，禁止自绘状态样式」条款）都需要落在真实功能页面上才能端到端验收；本 change 受「零视觉变更红线」约束（不改任何现有页面），而 /screening 等页面由后续功能 delta 实现 → 两者不能在本 change 内落地。
- 承接方式：由后续功能页 delta 的验收项承接上述 Scenario/条款（在相应 delta 中做真实页面级断言）；本 change 的当前覆盖为组件级测试——`aiBadge.test.tsx`（4 例）与 `statusBadge.test.tsx`（9 例）。

## 结论

- **机器验证：通过**——零视觉变更红线 14/14 在 ≤0.1% 容差（maxDiffPixelRatio 0.001）内 0 例上报差异（含对比工具能力自证）；前端 91 files / 761 tests 全绿；后端定向 222 通过；既有测试零修改（红线守住）。
- **人工签收待办**：①令牌美学（rise/fall 与既有 status-error/success 同色族的观感）；②五态徽章浅/深双主题对比度（尤其 degraded 琥珀描边、running 蓝底白字）；③异常记录 (b)(c) 的裁定（表格库与 SignedPct 组件是否需要）。

## 人工验证签字

- [ ] 抽查通过，确认令牌/徽章/壳符合预期、双主题观感可接受
- 签字/日期：____________
