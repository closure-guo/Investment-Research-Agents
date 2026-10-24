/**
 * add-composer-stat-pills / refine-llm-settings-pane 专项 E2E 配置（隔离端口 + 隔离库）。
 *
 * 为什么不复用默认 config：本机 8000/5173 被 docker compose 占用（真实数据），
 * 默认 config 的 reuseExistingServer 会把测试打到真实库上。本配置固定 8010/5182
 * 与 test-e2e-stats-* 独立库，避免污染任何开发/生产数据。
 */
import { defineConfig } from '@playwright/test'
import './test-env'

export default defineConfig({
  testDir: './tests',
  testMatch: ['composer-stat-pills.spec.ts', 'llm-settings-pane.spec.ts'],
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5182',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8010',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-stats-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-stats-snapshot.db',
        RESEARCH_REPORTS_DB_PATH: 'data/test-e2e-stats-research-reports.db',
        SCREENING_DB_PATH: 'data/test-e2e-stats-screening.db',
        FACTOR_LIBRARY_DB_PATH: 'data/test-e2e-stats-factor-library.db',
        EXECUTION_DB_PATH: 'data/test-e2e-stats-execution.db',
        REGIME_DB_PATH: 'data/test-e2e-stats-regime.db',
        EVENTS_DB_PATH: 'data/test-e2e-stats-events.db',
        ANALYSIS_CONCURRENCY_PATH: 'data/test-e2e-stats-analysis-concurrency.json',
        REPORTS_DIR: 'tmp/e2e-reports-8010',
      },
      url: 'http://localhost:8010/api/health',
      timeout: 60_000,
      reuseExistingServer: true,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5182',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8010' },
      url: 'http://localhost:5182',
      timeout: 60_000,
      reuseExistingServer: true,
      cwd: '../../../frontend',
    },
  ],
})
