import { defineConfig } from '@playwright/test'

/**
 * harden-react-path-resilience E2E 配置（pipeline 场景）
 *
 * 使用 8002/5177 端口对，STUB_SCENARIO=pipeline。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['harden-react-path-resilience.spec.ts'],
  grep: /8\.[12]/,
  timeout: 180_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5177',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8002',
      env: { TESTING: '1', STUB_SCENARIO: 'pipeline', STUB_NODE_DELAY: '1.5', SESSIONS_DB_PATH: 'data/test-e2e-sessions.db', SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db', REPORTS_DIR: 'tmp/e2e-reports-8002' },
      url: 'http://localhost:8002/api/health',
      timeout: 30_000,
      reuseExistingServer: true,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5177',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8002' },
      url: 'http://localhost:5177',
      timeout: 30_000,
      reuseExistingServer: true,
      cwd: '../../../frontend',
    },
  ],
})
