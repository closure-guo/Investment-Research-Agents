import { defineConfig } from '@playwright/test'

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
        // 因子库 E2E 造数库（同默认 config 口径）
        FACTOR_LIBRARY_DB_PATH: 'data/test-e2e-factor-library.db',
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
