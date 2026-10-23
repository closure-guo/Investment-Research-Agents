import { defineConfig } from '@playwright/test'
import './test-env'

/**
 * add-quick-mode-operator：快速模式系统操作 E2E 专属 config。
 *
 * 独立端口对（后端 8006 / 前端 5179）与专属 STUB_SCENARIO=operator，
 * 避免与默认门禁（8000/5173，可能是生产容器）及 timeline/resilience 套件冲突。
 * 全部 DB 路径指向 test-e2e-operator-*，不触碰开发/生产库。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['**/quick-operator.spec.ts'],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5179',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      command:
        'node tests/e2e/playwright/scripts/dev-supervisor.mjs --port 8006 -- uv run uvicorn finance_agent.api:app --port 8006',
      env: {
        TESTING: '1',
        STUB_SCENARIO: 'operator',
        SESSIONS_DB_PATH: 'data/test-e2e-operator-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-operator-snapshot.db',
        RESEARCH_REPORTS_DB_PATH: 'data/test-e2e-operator-research-reports.db',
        SCREENING_DB_PATH: 'data/test-e2e-operator-screening.db',
        FACTOR_LIBRARY_DB_PATH: 'data/test-e2e-operator-factor-library.db',
        EXECUTION_DB_PATH: 'data/test-e2e-operator-execution.db',
        REGIME_DB_PATH: 'data/test-e2e-operator-regime.db',
        EVENTS_DB_PATH: 'data/test-e2e-operator-events.db',
        FACTOR_RESEARCH_STATUS_PATH: 'data/test-e2e-operator-factor-research-status.json',
        FACTOR_RESEARCH_CONTROL_PATH: 'data/test-e2e-operator-factor-research-control.json',
        FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-operator-factor-research-launcher.json',
        REPORTS_DIR: 'tmp/e2e-operator-reports-8006',
      },
      url: 'http://localhost:8006/api/health',
      timeout: 60_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      command:
        'node ../tests/e2e/playwright/scripts/dev-supervisor.mjs --port 5179 -- npm run dev -- --port 5179',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8006' },
      url: 'http://localhost:5179',
      timeout: 60_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
