import { defineConfig } from '@playwright/test'
import './test-env'

/**
 * 收割链路 UI 专属套件（delta add-factor-harvest-ui T8）。
 *
 * 为什么需要专属 config，而不是留在默认套件（8000/5173）里：
 *
 * 1. **收割区块挂在主快照之下**：`FactorResearchPage.tsx` 里 supervisor / 收割 / 会话历史
 *    整块由 `data !== null && data.available` 门控，所以收割 E2E 必须先有一份
 *    `FACTOR_RESEARCH_STATUS_PATH` 快照，否则页面只显示「采集器未接入」，收割区块根本不存在。
 * 2. **该快照 fixture 在默认套件里是别人的**：`factor-research-monitor.spec.ts` 每个用例
 *    自己写、并在 `afterAll` 删除。两个文件并行时，收割用例过不过取决于调度时序——
 *    实测：单独跑默认 config 必红（页面无收割区块），随套件跑则靠 race 偶然通过。
 *    「绿灯但结果靠运气」正是本仓 flaky 收集器要抓的形态，不能就这么留着。
 * 3. 独立端口对（8007/5180）+ 独立 fixture 路径 + 单 worker 后，收割 E2E 与其它 spec
 *    零共享状态、结果确定；`reuseExistingServer: false` 保证不会误连用户本机 8000/5173
 *    的生产栈（与 playwright.isolated.config.ts 同款纪律）。
 *
 * 数据面纪律：**不 mock 业务接口**——监控快照与收割状态/候选一律由 spec 以真实文件写入
 * 下列隔离路径，后端仍走真实读文件路径（红线：E2E 禁止伪造业务接口响应）。
 */
export default defineConfig({
  testDir: './tests',
  testMatch: '**/factor-harvest-ui.spec.ts',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  // 单 worker + 非并行：spec 共享同一份 fixture（写 → 断言 → 删），并行会互踩
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5180',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8007',
      env: {
        TESTING: '1',
        // 独立 sessions 库：本套件不需要 ops 历史行（job 块缺失 → 收割「状态不可得」形态），
        // 但绝不能被并发套件的写入/清理影响（跨挂载 SQLite 争用的既有教训见 AGENTS.md）
        SESSIONS_DB_PATH: 'data/test-e2e-harvest-sessions.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db',
        // 监控快照 fixture（页面门控依赖它；spec 自己写/删，不与 monitor spec 共享）
        FACTOR_RESEARCH_STATUS_PATH: 'data/test-e2e-factor-research-status.json',
        FACTOR_RESEARCH_CONTROL_PATH: 'data/test-e2e-factor-research-control.json',
        FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-factor-research-launcher.json',
        FACTOR_RESEARCH_CONFIG_PATH: 'data/test-e2e-factor-research-pipeline.json',
        FACTOR_RESEARCH_DISTRO_PROBE_PATH: 'data/test-e2e-factor-research-distro-probe.json',
        // 收割数据面（本 delta 的被测对象）：状态/候选/批内心跳
        FACTOR_RESEARCH_HARVEST_STATE_PATH: 'data/test-e2e-factor-research-harvest-state.json',
        FACTOR_RESEARCH_CANDIDATES_PATH: 'data/test-e2e-factor-research-candidates.json',
        FACTOR_RESEARCH_HEARTBEAT_PATH: 'data/test-e2e-factor-research-heartbeat.json',
        // 报告落盘目录隔离，避免 E2E 副作用污染 reports/
        REPORTS_DIR: 'tmp/e2e-reports-8007',
      },
      url: 'http://localhost:8007/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5180',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8007' },
      url: 'http://localhost:5180',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
