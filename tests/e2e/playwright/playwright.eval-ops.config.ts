import { defineConfig } from '@playwright/test'

/**
 * 评估运维分区专属 E2E 配置（eval-ops-console.spec.ts 套件）
 *
 * 为什么独立 config（issue #252，沿 track-record 先例）：
 * 本套件 `POST /api/v1/ops/jobs/{id}/run` 在服务端**同步执行**（单发 20-60s，
 * 含 AKShare 网络重试退避），与默认套件并行运行时独占同一单进程后端 +
 * 共享 SQLite 写窗口，把流式用例打到随机红（trace 实证 POST /api/agui/quick
 * 返回 -1）。拆专属端口对 + 独立测试库做物理隔离后，默认套件恢复并行门禁，
 * 本套件串行单 worker 运行（单 spec，无争用）。
 *
 * 端口对 8005/5178（避开默认 8000/5173、timeline 8001-8003/5174-5176、
 * track-record 8004/5177）。专属测试库 data/test-e2e-eval-ops.db
 * （cohort 开关默认「未开启」前提与共享库互不影响）。
 * 本地重跑前需删 data/test-e2e-eval-ops.db*（serial 首用例依赖空库）。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['eval-ops-console.spec.ts'],
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],

  use: {
    baseURL: 'http://localhost:5178',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      // 后端：TESTING=1（不启动调度器，scheduler_running 恒 false——本套件前提），
      // 专属测试库与报告目录；不复用既有 server，保证拿到的必是本 config
      // 环境变量启动的实例（incident 038：复用探活会撞上同端口生产容器）
      // COHORT_ENABLED=0 显式钉「未开启」前提：backend 启动时 bootstrap_cohort_from_env
      // 会把 cwd .env 的 COHORT_ENABLED=1（生产配置）种子进全新测试库——本机从带
      // 生产 .env 的检出跑本套件时该种子会让 cohort 显示「已开启」连环红两条
      command: 'uv run uvicorn finance_agent.api:app --port 8005',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-eval-ops.db',
        REPORTS_DIR: 'tmp/e2e-reports-8005',
        COHORT_ENABLED: '0',
      },
      url: 'http://localhost:8005/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      // 前端：独立端口 5178，API 代理指向 8005 后端
      command: 'npm run dev -- --port 5178',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8005' },
      url: 'http://localhost:5178',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
