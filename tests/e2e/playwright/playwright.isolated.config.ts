import { defineConfig } from '@playwright/test'
import './test-env'

// 隔离端口下的 API 直连基址注入：部分 spec（factor-library / agui 族 / smoke 等）
// 用 `process.env.E2E_API_BASE ?? 'http://127.0.0.1:5173'` 直连 API——本机 5173/8000
// 是生产栈端口（本隔离配置起的栈在 5174/8001），不注入时这些 spec 会打穿生产
// （incident 038 家族；2026-10-27 实证：E2E 请求打到生产后端创建了真实会话）。
process.env.E2E_API_BASE ??= 'http://127.0.0.1:5174'

/**
 * 隔离配置：避免占用用户已在运行的 8000/5173 服务。
 * 后端 TESTING=1 起在 8001，前端起在 5174 并代理到 8001。
 *
 * SESSIONS_DB_PATH 必须指向独立文件：与 docker 生产后端共用 data/sessions.db
 * 会因两进程并发写导致 SQLite 主库被 WAL 帧覆盖而彻底损坏（不可恢复）。
 *
 * FACTOR_LIBRARY_DB_PATH（delta add-factor-library-page T2.3）：本 config 下跑
 * factor-library.spec.ts 时 seed/factors_reset 会清表——不指向独立库会落到开发库
 * data/factor_library.db（seed 造数/复位污染）。
 *
 * EXECUTION_DB_PATH（delta add-broker-console T2.3）：本 config 下跑
 * broker-console.spec.ts 时 seed/broker_reset 会清 orders/positions/cash 三表——
 * 同款纪律指向独立库（data/test-e2e-execution.db，与默认 config 同文件；
 * track-record config 用的是另一个文件，承载 paper_equity 造数互不影响）。
 *
 * SCREENING_DB_PATH（delta add-daily-ops-page T2.3）：本 config 下跑
 * daily-ops.spec.ts 时 seed/screening_reset 会清 screening 五表——不指向独立库会
 * 落到开发库 data/screening.db（与默认 config 同文件 data/test-e2e-screening.db，
 * .gitignore 既有条目覆盖）。
 *
 * REGIME_DB_PATH（delta add-crowding-regime-positioning T8）：本 config 下跑
 * market-regime.spec.ts 时 seed/regime_reset 会清 regime_daily 全表——同款纪律
 * 指向独立库（data/test-e2e-regime.db，与默认 config 同文件）。
 *
 * EVENTS_DB_PATH（delta add-event-driven-lane T8）：本 config 下跑
 * event-watchlist.spec.ts 时 seed/events_reset 会清事件线四表——同款纪律指向独立库
 * （data/test-e2e-events.db，与默认 config 同文件；.gitignore 既有条目覆盖）。
 */
export default defineConfig({
  testDir: './tests',
  testIgnore: 'thinking-timeline*.spec.ts',
  timeout: 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8001',
      env: {
        TESTING: '1',
        // 独立测试库，与生产 data/sessions.db 物理隔离
        SESSIONS_DB_PATH: 'data/test-e2e-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db',
        RESEARCH_REPORTS_DB_PATH: 'data/test-e2e-research-reports.db',
        // 因子库 E2E 造数库（同默认 config 口径）
        FACTOR_LIBRARY_DB_PATH: 'data/test-e2e-factor-library.db',
        // 执行台账 E2E 造数库（同默认 config 口径；broker_reset 清账户三表）
        EXECUTION_DB_PATH: 'data/test-e2e-execution.db',
        // 选股 E2E 造数库（同默认 config 口径；screening_reset 清五表）
        SCREENING_DB_PATH: 'data/test-e2e-screening.db',
        // 市场温度计 E2E 造数库（同默认 config 口径；regime_reset 清 regime_daily）
        REGIME_DB_PATH: 'data/test-e2e-regime.db',
        // 事件观察 E2E 造数库（同默认 config 口径；events_reset 清事件线四表）
        EVENTS_DB_PATH: 'data/test-e2e-events.db',
        // 因子研发文件桥（delta add-factor-research-settings：隔离配置同样不得读写生产
        // data/factor_research 下的快照/控制/心跳/节奏配置）
        FACTOR_RESEARCH_STATUS_PATH: 'data/test-e2e-factor-research-status.json',
        FACTOR_RESEARCH_CONTROL_PATH: 'data/test-e2e-factor-research-control.json',
        FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-factor-research-launcher.json',
        FACTOR_RESEARCH_CONFIG_PATH: 'data/test-e2e-factor-research-pipeline.json',
        // distro 探活文件（delta add-factor-research-distro-liveness-probe）：隔离配置同样
        // 不得读生产 data/factor_research/distro_probe.json（启动器会周期刷新它）
        FACTOR_RESEARCH_DISTRO_PROBE_PATH: 'data/test-e2e-factor-research-distro-probe.json',
        // 收割文件桥（delta add-factor-harvest-ui：spec fixture 写 test-e2e-* 文件名，
        // 与默认 config 逐字一致；缺这三项时隔离后端读 worktree 相对生产路径 →
        // factor-harvest-ui 的收割块永不渲染）
        FACTOR_RESEARCH_HARVEST_STATE_PATH: 'data/test-e2e-factor-research-harvest-state.json',
        FACTOR_RESEARCH_CANDIDATES_PATH: 'data/test-e2e-factor-research-candidates.json',
        FACTOR_RESEARCH_HEARTBEAT_PATH: 'data/test-e2e-factor-research-heartbeat.json',
        // 并发设置文件（delta add-analysis-concurrency-setting：隔离配置同样不得读写
        // 生产 data/settings/analysis_concurrency.json）
        ANALYSIS_CONCURRENCY_PATH: 'data/test-e2e-analysis-concurrency.json',
        // 隔离报告落盘目录，避免 E2E 副作用污染 reports/
        REPORTS_DIR: 'tmp/e2e-reports-8001',
      },
      url: 'http://localhost:8001/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5174',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8001' },
      url: 'http://localhost:5174',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
