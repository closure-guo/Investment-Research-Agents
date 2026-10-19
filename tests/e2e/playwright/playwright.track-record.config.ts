import { defineConfig } from '@playwright/test'
import './test-env'

/**
 * 战绩页专属 E2E 配置（track-record-*.spec.ts 套件 + paper-broker.spec.ts;
 * 现含 add-index-performance-compare / add-portfolio-beta-alpha / add-paper-broker
 * 三个 spec）
 *
 * 为什么独立 config（数据竞争，review fix）：
 * 本 spec 经 /api/test/seed 写 equity_curve/index_closes 造数行，而默认 config 的
 * 共享测试库（data/test-e2e-sessions.db）同时承载 decisions.spec.ts 的
 * 「无净值快照空态」断言（track-record-curve count 0）——默认套件 fullyParallel
 * 下两者互斥：种子一旦落库（且跨 run 持久），decisions.spec 空态必红；
 * /api/test/reset 为占位骨架无法自清理。故沿 timeline config 先例
 * （特殊前提 spec → 专属 config + 独立 CI step）为本套件拆专属库：
 * SESSIONS_DB_PATH=data/test-e2e-track-record.db，与共享库彻底互不影响。
 *
 * add-paper-broker（T4）：paper-broker.spec.ts 造数还写 execution 库
 * （track_record.paper_equity → paper_equity 表），故后端 env 追加
 * EXECUTION_DB_PATH 隔离——否则种子会落到开发库 data/execution.db（与
 * SCREENING_DB_PATH 同款纪律）；该 spec 每例前后经 track_record_reset 复位，
 * 种子不跨例残留。
 *
 * 端口对 8004/5177（避开默认 8000/5173 与 timeline 8001-8003/5174-5176）。
 *
 * 2026-10-13（E2E 修复）：不再要求「本地重跑前手动删 data/test-e2e-track-record.db*」
 * ——本 config 的 globalSetup（global-setup-track-record.ts）在测试前经 TESTING 端点
 * 全量复位（五张造数面表 + paper_equity），让本地重跑与 CI 全新起库等价。
 */
export default defineConfig({
  testDir: './tests',
  // strategy-scorecard.spec.ts（add-strategy-family-scorecards 的 track-record 门禁）
  // 文件名不带 track-record- 前缀，故在此显式收录（默认 config 同步显式排除）
  testMatch: ['track-record-*.spec.ts', 'paper-broker.spec.ts', 'strategy-scorecard.spec.ts'],
  timeout: 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  globalSetup: './global-setup-track-record.ts',

  use: {
    baseURL: 'http://localhost:5177',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      // 后端：TESTING=1（/api/test/seed 可用），专属测试库与报告目录，
      // 与共享库（test-e2e-sessions.db）隔离；不复用既有 server，
      // 保证拿到的必是本 config 环境变量启动的实例
      command: 'uv run uvicorn finance_agent.api:app --port 8004',
      env: {
        TESTING: '1',
        SESSIONS_DB_PATH: 'data/test-e2e-track-record.db',
        SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db',
        EXECUTION_DB_PATH: 'data/test-e2e-track-record-execution.db',
        REPORTS_DIR: 'tmp/e2e-reports-8004',
      },
      url: 'http://localhost:8004/api/health',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../',
    },
    {
      // 前端：独立端口 5177，API 代理指向 8004 后端（seed 走相对路径经此代理）
      command: 'npm run dev -- --port 5177',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8004' },
      url: 'http://localhost:5177',
      timeout: 30_000,
      reuseExistingServer: false,
      cwd: '../../../frontend',
    },
  ],
})
