import { defineConfig, devices } from '@playwright/test'
import './test-env'

/**
 * F2 E2E 门禁基础设施：双 webServer 拉起前后端
 *
 * 后端以 TESTING=1 启动，走 LLM stub 占位（完整 stub 在 F3 落地）
 * 前端走 vite dev server
 *
 * 设计决策见 openspec/changes/add-e2e-test-infrastructure/design.md
 */
export default defineConfig({
  testDir: './tests',
  // 时间序列/管线 spec 需要专属 config（playwright.timeline.config.ts，STUB_SCENARIO=tool_call/pipeline/llm_failure），
  // 在默认 config 下排除，避免无 STUB_SCENARIO 时失败
  testIgnore: [
    'thinking-timeline*.spec.ts',
    'harden-react-path-resilience.spec.ts',
    'persist-full-session-timeline.spec.ts',
    'pipeline-eta-banner.spec.ts',
    'pipeline-hierarchical-timeline.spec.ts',
    'resume-pipeline-across-sessions.spec.ts',
    // AG-UI quick 通道带工具调用 run 依赖 STUB_SCENARIO=tool_call 的后端（8001/5174），
    // 由 playwright.timeline.config.ts 运行，默认 config 的 stub 无工具场景，故排除
    'agui-toolcall.spec.ts',
    // 报告导出抽屉依赖 STUB_SCENARIO=pipeline 的 5 层管线后端（8002/5175），
    // 由 playwright.timeline.config.ts 运行，默认 config 无此后端，故排除
    'report-export.spec.ts',
    // 股价 K 线图同依赖 pipeline 环境（8002/5175），timeline config 运行，默认排除
    'report-kline-chart.spec.ts',
    // watch 触发位链路同依赖 pipeline 环境（8002/5175），timeline config 运行，默认排除
    'report-watch-trigger-lines.spec.ts',
    // 报告面板与主顶栏层叠同依赖 pipeline 环境（8002/5175），timeline config 运行，默认排除
    'report-panel-overlay.spec.ts',
    // 消息操作条（复制/重试/点赞/点踩）同依赖 8002/5175 pipeline 环境，
    // 由 playwright.timeline.config.ts 运行，默认 config 排除
    'message-actions.spec.ts',
    // 视觉基线截图采集（会话页/报告渲染态）同依赖 8002/5175 管线环境，
    // 由 playwright.timeline.config.ts 运行（未设 BASELINE_DIR 时自跳过），默认 config 排除
    'visual-baseline-report.spec.ts',
    // 管线卡视图切换（graph 默认 / 列表可切）同依赖 8002/5175 管线环境，
    // 由 playwright.timeline.config.ts 运行，默认 config 排除
    'pipeline-view-toggle.spec.ts',
    // 管线图缩放控件同依赖 8002/5175 管线环境，timeline config 运行，默认排除
    'pipeline-graph-zoom.spec.ts',
    // 以下为前置技术债：使用 waitForTimeout 的时序依赖测试，在 CI 上不稳定，
    // 需专属 STUB_SCENARIO 或在 timeline config 内运行。
    // 2026-09-04 技术债清偿：
    //   - concurrent-streaming-integrity 重写为 web-first 已回门禁；
    //   - session-switch-resumption 重写为 web-first 已回门禁；
    //   - debug-*×7 / explore 共 8 个一次性调试/探索脚本删除（debug-cursor 注明
    //     旧通道语义已迁移 AG-UI；switch-during-response/thinking/sse-continue
    //     的场景由 session-switch-resumption + concurrent-streaming-integrity 覆盖，
    //     见 incident 023「UI 通道迁移必须带走旧 E2E」教训的收尾）。
    // 2026-09-04 扩充：把全部 waitForTimeout 技术债 spec 移出门禁（此前仅排除了
    // 两个，导致 CI 门禁因这些不稳定 spec 长期变红——downloads stale testid 与
    // 本批 debug-*/explore/concurrent-streaming 均为已知问题，非代码回归）。
    // 以下为 @live 人工验证/时序敏感测试，stub 环境无法满足其前提（见 spec 内注释）：
    // - refresh-resume-accept：要求「流式进行中」刷新（真实 LLM 慢速流式下思考横幅
    //   持续可见）；stub 瞬时输出使「思考中」无可见窗口，仅能验证完成态渲染。
    //   按 spec 注释用 docker compose（真实 LLM）人工验证，CI stub 下跳过。
    // - refresh-concurrent-misalignment：固定连 5174（timeline 端口对），与默认
    //   config（5173）不匹配且 timeline 环境亦不稳定，属 pre-existing 孤儿测试。
    'refresh-resume-accept.spec.ts',
    'refresh-concurrent-misalignment.spec.ts',
    // 战绩页 track-record-*.spec.ts 造数会污染共享测试库的空态前提（decisions.spec 依赖），
    // 迁入 playwright.track-record.config.ts 独立测试库，见该文件头注释
    'track-record-*.spec.ts',
    // add-paper-broker T4：paper-broker.spec.ts 同属战绩页造数套件（equity_curve +
    // paper_equity 双曲线种子）→ 同迁入 track-record 专属 config（testMatch 显式收录，
    // 后端带 SESSIONS_DB_PATH + EXECUTION_DB_PATH 双隔离）
    'paper-broker.spec.ts',
    // add-strategy-family-scorecards：策略族成绩单 spec 同属战绩页造数面（每例
    // track_record_reset 清共享库的 predictions/paper_equity）→ 归入 track-record
    // 专属 config（那边 testMatch 显式收录）；文件名无 track-record- 前缀，故此处单列
    'strategy-scorecard.spec.ts',
    // eval-ops 手动补跑用例在服务端同步执行 20-60s（AKShare 重试退避），与本套件并行时
    // 独占单进程后端+共享 SQLite 写窗口致流式用例随机红（issue #252，trace 实证）——
    // 迁入 playwright.eval-ops.config.ts 专属端口对（8005/5178）物理隔离，默认套件恢复并行门禁
    'eval-ops-console.spec.ts',
  ],
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  webServer: [
    {
      // Python 后端：TESTING=1 开启测试模式（/api/test/* 端点可用，LLM stub 占位）
      // SESSIONS_DB_PATH 指向独立测试库：与生产 data/sessions.db 共用同一文件时，
      // 两个后端进程并发写会导致 SQLite 主库被 WAL 帧覆盖而彻底损坏（不可恢复）
      // SCREENING_DB_PATH 同理指向独立测试库（add-screening-pipeline T5）：选股 E2E
      // 的 seed/screening_reset 会清表，绝不能落到开发库 data/screening.db
      // FACTOR_LIBRARY_DB_PATH 同理（delta add-factor-library-page T2.3）：因子库 E2E 的
      // seed/factors_reset 会清表，绝不能落到开发库 data/factor_library.db
      // EXECUTION_DB_PATH 同理（delta add-broker-console T2.3）：执行台账 E2E 的
      // seed/broker_reset 会清 orders/positions/cash 三表，绝不能落到开发库
      // data/execution.db（与 track-record config 的 EXECUTION_DB_PATH 是不同文件：
      // 那边承载 paper_equity 双曲线造数，互不影响）
      // FACTOR_RESEARCH_STATUS_PATH 同理（delta add-factor-research-monitor T4）：因子研发
      // 监控 E2E 的 fixture 快照由 spec 直接写文件，绝不能落到生产快照
      // data/factor_research/status.json（采集器运行时产物）；LAUNCHER_PATH 同纪律
      // （delta add-factor-research-selfheal：心跳 fixture 也不得落生产 launcher.json）
      // REGIME_DB_PATH 同理（delta add-crowding-regime-positioning T8）：市场温度计 E2E 的
      // seed/regime_reset 会清 regime_daily 全表，绝不能落到开发库 data/regime.db
      // EVENTS_DB_PATH 同理（delta add-event-driven-lane T8）：事件观察区块 E2E 的
      // seed/events_reset 会清事件线四表，绝不能落到开发库 data/events.db
      command: 'uv run uvicorn finance_agent.api:app --port 8000',
      env: { TESTING: '1', SESSIONS_DB_PATH: 'data/test-e2e-sessions.db', SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db', RESEARCH_REPORTS_DB_PATH: 'data/test-e2e-research-reports.db', SCREENING_DB_PATH: 'data/test-e2e-screening.db', FACTOR_LIBRARY_DB_PATH: 'data/test-e2e-factor-library.db', EXECUTION_DB_PATH: 'data/test-e2e-execution.db', REGIME_DB_PATH: 'data/test-e2e-regime.db', EVENTS_DB_PATH: 'data/test-e2e-events.db', FACTOR_RESEARCH_STATUS_PATH: 'data/test-e2e-factor-research-status.json', FACTOR_RESEARCH_CONTROL_PATH: 'data/test-e2e-factor-research-control.json', FACTOR_RESEARCH_LAUNCHER_PATH: 'data/test-e2e-factor-research-launcher.json', REPORTS_DIR: 'tmp/e2e-reports-8000' },
      url: 'http://localhost:8000/api/health',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../',
    },
    {
      command: 'npm run dev -- --port 5173',
      url: 'http://localhost:5173',
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      cwd: '../../../frontend',
    },
  ],
})
