import { defineConfig } from '@playwright/test'
import './test-env'

/**
 * add-research-report-ingest：研报投喂页 E2E 专属 config。
 *
 * 独立端口对（后端 8007 / 前端 5180），全部 DB 路径指向 test-e2e-ingest-*，
 * 不触碰开发/生产库与其他套件端口。投喂用无文本层 PDF（解析降级 → 不触发
 * LLM 抽取），保持确定性、不依赖外部 LLM。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['**/research-ingest.spec.ts'],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5180',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      command:
        'node tests/e2e/playwright/scripts/dev-supervisor.mjs --port 8007 -- uv run uvicorn finance_agent.api:app --port 8007',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-ingest-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-ingest-snapshot.db',
        RESEARCH_REPORTS_DB_PATH: 'data/test-e2e-ingest-research-reports.db',
        SCREENING_DB_PATH: 'data/test-e2e-ingest-screening.db',
        EXECUTION_DB_PATH: 'data/test-e2e-ingest-execution.db',
        REGIME_DB_PATH: 'data/test-e2e-ingest-regime.db',
        EVENTS_DB_PATH: 'data/test-e2e-ingest-events.db',
        REPORTS_DIR: 'tmp/e2e-ingest-reports-8007',
      },
      url: 'http://localhost:8007/api/health',
      timeout: 60_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      command:
        'node ../tests/e2e/playwright/scripts/dev-supervisor.mjs --port 5180 -- npm run dev -- --port 5180',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8007' },
      url: 'http://localhost:5180',
      timeout: 60_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
