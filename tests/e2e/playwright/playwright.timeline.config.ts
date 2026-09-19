import { defineConfig } from '@playwright/test'

/**
 * 思考-搜索-思考 时间序列专用 E2E 配置（agent-turn-box-display delta 复现测试）
 *
 * 与全局 playwright.config.ts 隔离，使用独立端口对（后端 8001 / 前端 5174），
 * 避免与全局 webServer（8000/5173）冲突，也避免 STUB_SCENARIO=tool_call
 * 影响其他确定性 spec（streaming/contract 等依赖 stub 1 轮完成）。
 *
 * 后端以 TESTING=1 STUB_SCENARIO=tool_call 启动：
 *   - StubLLMClient 走工具调用场景（思考1 -> tool_call(web_search) -> 思考2 -> 回答）
 *   - TESTING=1 时注册 stub web_search 工具（固定结果，不调真实 Tavily）
 * 前端通过 VITE_API_TARGET 指向 8001 后端。
 */
export default defineConfig({
  testDir: './tests',
  // 运行时间序列专用 spec（快速/深度/历史恢复三个场景）
  // + 管线分组（thinking-timeline-pipeline）、ETA/横幅（pipeline-eta-banner）
  // 与分层时间轴（pipeline-hierarchical-timeline）spec
  // + 切换会话恢复管线（resume-pipeline-across-sessions）spec
  testMatch: [
    'thinking-timeline*.spec.ts',
    'pipeline-eta-banner.spec.ts',
    'pipeline-hierarchical-timeline.spec.ts',
    'resume-pipeline-across-sessions.spec.ts',
    'persist-full-session-timeline.spec.ts',
    'harden-react-path-resilience.spec.ts',
    // AG-UI quick 通道带工具调用 run（fix/agui-quick-toolcall-lifecycle 回归）：
    // 依赖 STUB_SCENARIO=tool_call 后端（8001），覆盖 TOOL_CALL_END / 多轮分列 / 刷新恢复
    'agui-toolcall.spec.ts',
    // 报告导出抽屉：依赖 STUB_SCENARIO=pipeline 的 5 层管线后端（8002/5175）
    'report-export.spec.ts',
    // 消息操作条（复制/重试/点赞/点踩）：同 pipeline 环境(8002/5175),深度摘要经
    // AnalysisThread 渲染 stream-output 后 hover 断言操作条
    'message-actions.spec.ts',
    // 视觉基线截图采集（会话页/报告渲染态）：同 report-export 环境（8002/5175），
    // 未设 BASELINE_DIR 时整组自跳过（一次性采集工具，不进常规门禁）
    'visual-baseline-report.spec.ts',
    // 管线卡视图切换：默认 graph（add-pipeline-graph-view）+ 切列表时间轴渲染，
    // 同 pipeline 环境（8002/5175）
    'pipeline-view-toggle.spec.ts',
  ],
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  // 本套件含深度管线（stub 每节点 1.5s）与大量「运行中/切换中」时序断言，
  // 冷启动与慢 runner 下历史多次单点抖动致红（已修根因：FM stub 契约、视图默认、
  // 指针/列表时序假设）。retries=1 只吸收抖动——确定性回归两次都会失败，仍被发现；
  // 与默认 config 的 CI 口径一致（playwright.config.ts: retries: CI ? 1 : 0）
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      // 后端：TESTING=1 + STUB_SCENARIO=tool_call，确定性模拟思考-搜索-思考
      command: 'uv run uvicorn finance_agent.api:app --port 8001',
      env: { TESTING: '1', STUB_SCENARIO: 'tool_call', SESSIONS_DB_PATH: 'data/test-e2e-sessions.db', REPORTS_DIR: 'tmp/e2e-reports-8001' },
      url: 'http://localhost:8001/api/health',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../',
    },
    {
      // 前端：独立端口 5174，API 代理指向 8001 stub 后端
      command: 'npm run dev -- --port 5174',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8001' },
      url: 'http://localhost:5174',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../frontend',
    },
    // ── 管线分组 E2E（agent-turn-box-display delta task 5.5）──
    // 独立端口对（后端 8002 / 前端 5175），STUB_SCENARIO=pipeline 确定性触发
    // 5 层深度分析管线（管线内部 LLM/数据由 _llm_utils/fetch 的 TESTING stub 接管）。
    // 与 tool_call 场景隔离：两组后端 STUB_SCENARIO 不同，不能共用端口。
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8002',
      env: { TESTING: '1', STUB_SCENARIO: 'pipeline', STUB_NODE_DELAY: '1.5', SESSIONS_DB_PATH: 'data/test-e2e-sessions.db', REPORTS_DIR: 'tmp/e2e-reports-8002' },
      url: 'http://localhost:8002/api/health',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5175',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8002' },
      url: 'http://localhost:5175',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../frontend',
    },
    // ── LLM 失败场景（harden-react-path-resilience 8.3）──
    // 独立端口对（后端 8003 / 前端 5176），STUB_SCENARIO=llm_failure
    // LLM 在第 1 轮 raise 异常，验证前端展示错误信息而非无限等待。
    {
      command: 'uv run uvicorn finance_agent.api:app --port 8003',
      env: { TESTING: '1', STUB_SCENARIO: 'llm_failure', SESSIONS_DB_PATH: 'data/test-e2e-sessions.db', REPORTS_DIR: 'tmp/e2e-reports-8003' },
      url: 'http://localhost:8003/api/health',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5176',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8003' },
      url: 'http://localhost:5176',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../frontend',
    },
  ],
})
