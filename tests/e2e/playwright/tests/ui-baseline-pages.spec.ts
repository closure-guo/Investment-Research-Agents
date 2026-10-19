import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'

/**
 * unify-ui-design-system T1：打底前全页面视觉基线（浅色 + 深色）
 *
 * 定位：本 spec 不是行为断言套件，而是「零视觉变更红线」的存档 + 像素对比工具：
 * - 首次运行（由 playwright.ui-baseline.config.ts 拉起 8006/5179 隔离环境）加
 *   --update-snapshots 生成基线 PNG，落 tests/validation/ui-baseline/pages/（git 跟踪）；
 * - 打底完成后用同一 config 直接重跑即逐页像素对比（容差 ≤0.1% 像素），
 *   失败时 actual/diff 落 test-results 供人工核验。
 *
 * 环境硬约束：本 spec 会调 track_record_reset / screening_reset（真实接口，不 mock），
 * 必须跑在专属测试库（data/test-e2e-ui-baseline*.db，含 CACHE_DB_PATH 隔离的
 * data cache 库）上——被默认 config 扫到时整组跳过（guard 见下），杜绝在共享/开发库
 * 上执行 reset。设置中心缓存/监控两分区直接展示 data cache 库统计，只有隔离后
 * 才是确定值（0 / — / 空表文案），否则会读到外部进程写入的真实条目。
 *
 * 确定性口径（每次截图前都满足）：
 * - 主题：init script 预置 localStorage 的 fa_theme（键定义见 frontend/src/theme.ts），
 *   浅色/深色各一套，不依赖系统偏好；截图前断言 <html> 的 .dark 类与主题一致；
 * - 视口：1440×900（与 config use.viewport 同值）；colorScheme/timezoneId 亦由 config 钉死；
 * - Font Awesome：index.html 经 cdnjs 外链加载，等样式 + 字体真正生效再截图
 *   （防止「CDN 未加载完 → 图标缺失」的偶发差异）；
 * - 侧栏：等 /api/sessions 返回（骨架 → 「暂无历史会话」）且 framer-motion 入场
 *   动画落定（JS 驱动动画不受截图 animations:'disabled' 覆盖）；
 * - 每页各自锚点（见 CAPTURES），等真实数据到达后的稳定终态；/track-record 另经
 *   settle 钩子等成绩单区块（GET /api/scorecards）落定、断言其族空态文案，并把窗口
 *   钉回顶部（App 外壳的 scroll-to-bottom 竞态会让 sticky 侧栏在成图中位移）；
 * - 无 waitForTimeout、无 route mock（stub 由后端 TESTING=1 承担）。
 *
 * 矩阵（页面 × 主题 → 基线文件名，共 7 × 2 = 14 张）：
 *   /               home-light/dark.png               空态首页（EmptyState）
 *   /downloads      downloads-light/dark.png          下载中心空态（REPORTS_DIR 专属空目录）
 *   /track-record   track-record-light/dark.png       空战绩页（先 track_record_reset）
 *   /settings       settings-llm-light/dark.png       设置中心默认分区（LLM 配置）
 *                   settings-cache-light/dark.png     缓存管理分区（settings-nav-cache）
 *                   settings-monitor-light/dark.png   数据监控分区（settings-nav-monitor）
 *   /screening      screening-light/dark.png          空选股池（先 screening_reset；
 *                                                      温度计专属空库 → 「未接入」空态；
 *                                                      事件观察专属空库 → 「清单为空」空态）
 *
 * Mask（唯一一处，非跳过页面）：
 * - /track-record 总览「截至」卡显示后端 _track_as_of（北京时间当天，跨日必变）→
 *   以 getByText(/^\d{4}-\d{2}-\d{2}$/) 掩掉该裸日期文本。mask 在基线与对比两次
 *   运行中同样生效，仅掩盖这一处日期，其余像素全部参与对比。
 * 无其他 mask：专属 cache 库为空的前提下，缓存/监控分区的计数、时间戳均为
 * 确定值（0 / — / 空表文案）。
 *
 * 未采集页面（有意排除，非静默丢弃）：
 * - /track-record/calibration、/track-record/predictions/{id}：打底前无预测数据时
 *   均为纯空态/依赖 prediction_id 的详情态，本 change 的零视觉变更红线覆盖
 *   /track-record 与 /settings 主轴已足够；如后续 delta 触达这两页，矩阵应补采。
 */

const API_BASE = 'http://127.0.0.1:5179' // 显式 IPv4：Windows 上 localhost 可能先解析 ::1（见 screening-page.spec.ts 注）
const VIEWPORT = { width: 1440, height: 900 }
const DEDICATED_CONFIG = 'playwright.ui-baseline.config.ts'

type Theme = 'light' | 'dark'

type Capture = {
  /** 基线文件名前缀（最终名 `${name}-${theme}.png`） */
  name: string
  url: string
  /** 数据到达的稳定锚点（web-first，自动重试） */
  anchor: (page: Page) => Locator
  /** 进页就绪后的额外交互（设置中心切分区） */
  act?: (page: Page) => Promise<void>
  /** 截图前等新增异步区块落定（可选；见 track-record 成绩单区块：晚于总览锚点到达） */
  settle?: (page: Page) => Promise<void>
  /** 截图前复位数据前提（经 /api/test/seed 真实接口） */
  reset?: (request: APIRequestContext) => Promise<void>
  /** 文档化 mask（见文件头：仅 track-record 的当天日期） */
  masks?: (page: Page) => Locator[]
}

// 专属 config 守卫：默认 config 的 testIgnore 未收录本 spec，被扫到时会在共享
// 测试库上执行 reset、并按默认模板路径写图 → 整组跳过。
// （T1 纪律禁止改动既有 playwright.config.ts，故守卫收在本文件内。）
test.beforeEach(({}, testInfo) => {
  test.skip(
    !(testInfo.config.configFile ?? '').endsWith(DEDICATED_CONFIG),
    `仅限 ${DEDICATED_CONFIG}（隔离端口对 8006/5179）运行`,
  )
})

async function resetTrackRecord(request: APIRequestContext) {
  // 2026-10-13 起 track_record_reset 为**全量复位**（五张造数面表 + paper_equity，
  // 见 /api/test/seed 文档）：空态前提不再依赖「专属库无其他写入方」，基线状态更确定。
  const resp = await request.post(`${API_BASE}/api/test/seed`, { data: { track_record_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function resetScreening(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, { data: { screening_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

/** <html> 主题类与采集目标一致（App 挂载时按 fa_theme toggle .dark） */
async function waitForTheme(page: Page, theme: Theme) {
  const html = page.locator('html')
  if (theme === 'dark') await expect(html).toHaveClass(/\bdark\b/)
  else await expect(html).not.toHaveClass(/\bdark\b/)
}

/** Font Awesome 样式 + 字体就绪（cdnjs 外链，etag 缓存按浏览器上下文隔离） */
async function waitForFontAwesome(page: Page) {
  await expect(page.locator('i.fas').first()).toHaveCSS('font-family', /Font Awesome/)
  await page.evaluate(async () => {
    await document.fonts.ready
  })
}

/** 应用外壳就绪：侧栏退出骨架（/api/sessions 返回）且 JS 驱动入场动画落定 */
async function waitForShellReady(page: Page) {
  await expect(page.getByText('暂无历史会话')).toBeVisible()
  await expect(page.getByTestId('session-list')).toHaveCSS('opacity', '1')
}

async function gotoWithTheme(page: Page, url: string, theme: Theme) {
  // fa_theme 键见 frontend/src/theme.ts；init script 先于页面脚本执行
  await page.addInitScript((t: string) => {
    localStorage.setItem('fa_theme', t)
  }, theme)
  await page.setViewportSize(VIEWPORT)
  await page.goto(url)
  await waitForFontAwesome(page)
  // 先等 React 挂载 + 取数完成，再断言主题类——否则 light 分支的
  // not.toHaveClass 会在 App 生效前恒真通过（起不到护栏作用）
  await waitForShellReady(page)
  await waitForTheme(page, theme)
}

const CAPTURES: Capture[] = [
  {
    name: 'home',
    url: '/',
    anchor: (page) => page.getByTestId('empty-state'),
  },
  {
    name: 'downloads',
    url: '/downloads',
    anchor: (page) => page.getByTestId('downloads-empty'),
  },
  {
    name: 'track-record',
    url: '/track-record',
    anchor: (page) => page.getByTestId('track-record-summary'),
    reset: resetTrackRecord,
    // add-strategy-family-scorecards 增补（2026-10-18 基线重采）：成绩单区块自带
    // GET /api/scorecards 取数，晚于总览锚点到达——只等总览会在「加载中…」与终态之间
    // 抢跑（区块高度随状态变化 → fullPage 截图整体高度不稳）。故截图前显式等该区块落定，
    // 并把「reset 后必为确定态」钉死：两族数据面（predictions/paper_equity）均被
    // track_record_reset 清空 → 默认选中族走族空态「暂无样本」——其中不含 as_of 日期、
    // 容量金额等随时间/外部变化的读数，故无需额外 mask（四种原因码文案由前端 REASON_LABEL
    // 常量表渲染，无本地化/时间成分）。
    settle: async (page) => {
      await expect(page.getByTestId('scorecard-family-tabs')).toBeVisible()
      await expect(page.getByTestId('scorecard-empty')).toHaveText('暂无样本')
      // 捕获口径钉死（2026-10-18 基线重采时复现，证据见 task-6-report.md）：
      // ① 竞态——App 外壳挂载即调度一次 scroll-to-bottom（frontend/src/App.tsx
      //    的 scrollToBottom；deps=[messages] 使其在非聊天页也执行一次），滚动目标是
      //    当时刻的 document.body.scrollHeight，本页高度又随异步区块（成绩单/指数对比等）
      //    到达而增长 → 同一版本实测 scrollY 会落在 222 或 300 两态；
      // ② 成像——本页侧栏为 sticky，fullPage 截图在「文档全高」视口下绘制，成图中侧栏
      //    整体随 scrollY 等量下移（实测 scrollY +300 → 侧栏下移 300px，可复现），
      //    于是滚动竞态直接变成基线假红。
      // 故截图前把窗口钉回顶部并断言生效：基线口径固定为「页面顶部态」，不再随竞态漂移。
      await page.evaluate(() => window.scrollTo(0, 0))
      expect(await page.evaluate(() => window.scrollY)).toBe(0)
    },
    masks: (page) => [page.getByTestId('track-record-summary').getByText(/^\d{4}-\d{2}-\d{2}$/)],
  },
  {
    name: 'settings-llm',
    url: '/settings',
    anchor: (page) => page.getByTestId('llm-config-pane'),
  },
  {
    name: 'settings-cache',
    url: '/settings',
    anchor: (page) => page.getByTestId('cache-pane'),
    act: async (page) => {
      await page.getByTestId('settings-nav-cache').click()
    },
  },
  {
    name: 'settings-monitor',
    url: '/settings',
    anchor: (page) => page.getByTestId('data-monitor-pane'),
    act: async (page) => {
      await page.getByTestId('settings-nav-monitor').click()
    },
  },
  {
    name: 'screening',
    url: '/screening',
    anchor: (page) => page.getByTestId('screening-empty'),
    reset: resetScreening,
    // add-crowding-regime-positioning T8 增补（2026-10-18 基线重采）：市场温度计区块
    // 是滚动容器首子节点，自带 GET /api/regime 取数，晚于 screening-empty 锚点到达——
    // 只等锚点会在「加载中…」与终态之间抢跑（同一版本像素随状态漂移）。专属库
    // REGIME_DB_PATH=data/test-e2e-ui-baseline-regime.db 为**空库** → 该区块必为确定的
    // missing 空态「温度计未接入：暂无市场状态数据」，不含日期/金额等随时间变化的
    // 读数，故无需额外 mask（mask 与容差均未改动）。此处把前提钉死：若未来造数写入
    // 该库，显式失败而不是让基线悄悄漂移。
    settle: async (page) => {
      await expect(page.getByTestId('market-regime-card')).toBeVisible()
      await expect(page.getByTestId('regime-empty')).toHaveText(
        '温度计未接入：暂无市场状态数据（等待日批采集）'
      )
      // add-event-driven-lane T8 增补（2026-10-18 基线二次重采）：事件观察区块紧随
      // 温度计之后，自带 GET /api/events/watchlist 取数，同样晚于 screening-empty 锚点
      // 到达（区块高度随「加载中…/清单行/空态」变化 → fullPage 成图整体高度不稳）。
      // 专属库 EVENTS_DB_PATH=data/test-e2e-ui-baseline-events.db 为**空库** → 必为
      // 确定的空态文案（无日期/金额类读数，故无需额外 mask；mask 与容差均未改动）。
      // 同款钉死前提：若未来造数写入该库，此处显式失败而不是让基线悄悄漂移。
      await expect(page.getByTestId('event-watchlist-card')).toBeVisible()
      await expect(page.getByTestId('event-watchlist-empty')).toHaveText(
        '事件观察清单为空：暂无通过分诊的事件（等待日批抓取与分诊）'
      )
    },
  },
]

test.describe('打底前页面视觉基线（unify-ui-design-system T1）', () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const capture of CAPTURES) {
      test(`${capture.name} · ${theme}`, async ({ page, request }) => {
        if (capture.reset) await capture.reset(request)
        await gotoWithTheme(page, capture.url, theme)
        if (capture.act) await capture.act(page)
        await expect(capture.anchor(page)).toBeVisible()
        if (capture.settle) await capture.settle(page)
        await expect(page).toHaveScreenshot(`${capture.name}-${theme}.png`, {
          fullPage: true,
          animations: 'disabled',
          // 与 config expect.toHaveScreenshot.maxDiffPixelRatio 同值，此处显式重申红线容差
          maxDiffPixelRatio: 0.001,
          mask: capture.masks?.(page) ?? [],
        })
      })
    }
  }
})
