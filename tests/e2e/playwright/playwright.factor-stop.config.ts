import { defineConfig } from '@playwright/test'
import './test-env'

/**
 * 停止按钮 UI 专属套件（delta add-factor-research-stop-button）。
 *
 * 为什么需要专属 config，而不是留在默认套件（8000/5173）里：
 *
 * 1. 默认 config `reuseExistingServer: !CI`——本机生产栈常驻 8000/5173，跑默认套件
 *    会静默复用生产后端（incident 038/045 同族陷阱）；本用例会真实 PUT 控制文件，
 *    复用生产栈即可能把真实研发看护停掉。独立端口对（8009/5182）+
 *    `reuseExistingServer: false` 保证测试的是本 spec 自己写 fixture 的隔离后端
 *    （playwright.monitor-disk.config.ts / playwright.harvest.config.ts 同款纪律）。
 * 2. grep 只选中本 delta 的停止按钮用例（标题含「停止按钮」），其余 monitor 用例仍归
 *    默认套件，避免双跑。
 *
 * 数据面纪律：**不 mock 业务接口**——快照/控制文件由 spec 以真实文件读写隔离路径，
 * 后端仍走真实端点 + 文件桥（红线：E2E 禁止伪造业务接口响应）。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: '**/factor-research-monitor.spec.ts',
  grep: /停止按钮/,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5182',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8009',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-stop-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-stop-snapshot.db',
        FACTOR_RESEARCH_STATUS_PATH: 'data/test-e2e-factor-research-status.json',
        FACTOR_RESEARCH_CONTROL_PATH: 'data/test-e2e-factor-research-control.json',
        FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-factor-research-launcher.json',
        FACTOR_RESEARCH_CONFIG_PATH: 'data/test-e2e-factor-research-pipeline.json',
        FACTOR_RESEARCH_DISTRO_PROBE_PATH: 'data/test-e2e-factor-research-distro-probe.json',
        FACTOR_RESEARCH_HARVEST_STATE_PATH:
          'data/test-e2e-factor-research-harvest-state.json',
        FACTOR_RESEARCH_CANDIDATES_PATH: 'data/test-e2e-factor-research-candidates.json',
        FACTOR_RESEARCH_HEARTBEAT_PATH: 'data/test-e2e-factor-research-heartbeat.json',
        REPORTS_DIR: 'tmp/e2e-reports-8009',
      },
      url: 'http://localhost:8009/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5182 --strictPort',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8009' },
      url: 'http://localhost:5182',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
