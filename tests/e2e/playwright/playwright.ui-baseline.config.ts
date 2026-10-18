import { defineConfig } from '@playwright/test'

/**
 * 打底前全页面视觉基线专属 E2E 配置（unify-ui-design-system T1）
 *
 * 定位：本 config + ui-baseline-pages.spec.ts 是「零视觉变更红线」的存档与
 * 对比工具，不是常规 E2E 门禁断言套件：
 * - 首次运行 `--update-snapshots` 生成基线 PNG，提交到
 *   tests/validation/ui-baseline/pages/（git 跟踪，spec 场景规定目录）；
 * - 后续用同一 config 直接重跑即逐页像素对比（容差 ≤0.1% 像素：
 *   maxDiffPixelRatio = 0.001，见 openspec/changes/unify-ui-design-system
 *   specs/frontend/spec.md「视觉基线对比」场景）；失败时 actual/diff 落
 *   test-results 供人工核验。
 *
 * 为什么独立 config（沿 timeline 8001-8003 / track-record 8004 / eval-ops 8005 先例）：
 * - 端口对 8006/5179：避开默认 8000/5173（docker compose 生产栈可能占用）
 *   与既有各隔离套件，物理隔离互踩；
 * - 专属测试库 data/test-e2e-ui-baseline*.db：本套件会调 track_record_reset /
 *   screening_reset，绝不能落到开发库（data/screening.db 等）或共享 E2E 库；
 * - reuseExistingServer: false：保证探活拿到的必是本 config env 启动的实例
 *   （incident 038：复用探活会撞上同端口生产容器）；
 * - testMatch 只收 ui-baseline-pages.spec.ts：采集/对比必须串行单 worker，
 *   与其他套件并行会因侧栏骨架、后端缓存计数等互相污染截图。
 *
 * 为什么不进 CI（2026-10-13 决策）：像素基线在 Windows 本地采集，Linux runner 的
 * 字体/抗锯齿渲染差异会超 0.1% 容差产生假红；跨平台对比需按平台分基线维护
 * （{platform} 模板 + 各平台基线），仓内暂无该机制。定位 = UI 改动时的本地门禁
 * （改前采集 / 改后对比），不进 CI；若要 CI 化，先解决跨平台基线维护。
 *
 * 用法：
 *   生成基线：npx playwright test -c playwright.ui-baseline.config.ts --update-snapshots
 *   对比验证：npx playwright test -c playwright.ui-baseline.config.ts
 * 基线落点由 expect.toHaveScreenshot.pathTemplate 钉到
 * <repo>/tests/validation/ui-baseline/pages/{arg}{ext}（相对路径按 configDir 解析）。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['ui-baseline-pages.spec.ts'],
  timeout: 90_000,
  expect: {
    timeout: 15_000,
    toHaveScreenshot: {
      // 红线容差：允许的抗锯齿噪声 ≤ 0.1% 像素（spec「视觉基线对比」场景）
      maxDiffPixelRatio: 0.001,
      animations: 'disabled',
      // 仅本 kind（page 截图）走专属基线目录：{testDir} 为
      // tests/e2e/playwright/tests，向上三级到 repo 根 → tests/validation/ui-baseline/pages/
      pathTemplate: '{testDir}/../../../validation/ui-baseline/pages/{arg}{ext}',
    },
  },
  fullyParallel: false,
  // 采集/对比串行单 worker：套件内多例共享同一后端与截图口径，串行保证确定性
  workers: 1,
  retries: 0,
  // 仅 list：html reporter 与默认套件共用 playwright-report/ 目录，会互相覆盖
  reporter: [['list']],

  use: {
    baseURL: 'http://localhost:5179',
    // 固定视口：截图尺寸参与像素对比，任何隐式默认（1280×720）变更都会全线报红
    viewport: { width: 1440, height: 900 },
    // 被测主题由 localStorage fa_theme 驱动（frontend/src/theme.ts）；
    // 钉死系统级 colorScheme 与时区，排除机器环境对 prefers-color-scheme /
    // 本地时间渲染的干扰
    colorScheme: 'light',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      // 后端：TESTING=1（/api/test/seed 的 track_record_reset / screening_reset 可用），
      // 六个库路径全部指向专属测试库，REPORTS_DIR 指向专属空目录（/downloads 空态前提）。
      // CACHE_DB_PATH（src/finance_agent/data/cache.py 调用期读 env）是第五个库：
      // 设置中心「缓存管理」「数据监控」两分区展示 cache.db 统计，若不隔离会读到
      // 开发/生产进程写入的条目（首轮实测：17 条 / 360 KB 外部数据入镜），
      // 截图随外部进程写入与跨日过期而变化 → 必须隔离。
      // REGIME_DB_PATH（delta add-crowding-regime-positioning T8）是第六个库：
      // 选股页首屏渲染市场温度计卡片（GET /api/regime），不隔离会读到开发库
      // data/regime.db 的当日行（随日批/跨日变化）；指向专属**空库** → 卡片呈
      // 确定的「温度计未接入（missing）」空态，供视觉基线复现。
      // EVENTS_DB_PATH（delta add-event-driven-lane T8）是第七个库：选股页首屏还渲染
      // 事件观察区块（GET /api/events/watchlist），不隔离会读到开发库 data/events.db
      // 的既有清单（随日批变化）；指向专属**空库** → 区块呈确定的「清单为空」空态。
      command: 'uv run uvicorn finance_agent.api:app --port 8006',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-ui-baseline.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-ui-baseline-snapshot.db',
        SCREENING_DB_PATH: 'data/test-e2e-ui-baseline-screening.db',
        EXECUTION_DB_PATH: 'data/test-e2e-ui-baseline-execution.db',
        CACHE_DB_PATH: 'data/test-e2e-ui-baseline-cache.db',
        REGIME_DB_PATH: 'data/test-e2e-ui-baseline-regime.db',
        EVENTS_DB_PATH: 'data/test-e2e-ui-baseline-events.db',
        REPORTS_DIR: 'tmp/e2e-reports-8006',
      },
      url: 'http://localhost:8006/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      // 前端：独立端口 5179，API 代理指向 8006 后端（seed/reset 走相对路径经此代理）
      command: 'npm run dev -- --port 5179',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8006' },
      url: 'http://localhost:5179',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
