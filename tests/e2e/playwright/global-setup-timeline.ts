import { request } from '@playwright/test'

/**
 * 时间轴套件预热（2026-10-13，E2E 修复）。
 *
 * 证据：`pipeline-hierarchical-timeline` / `pipeline-eta-banner` 断言的是**运行中瞬态**
 * （timeline 挂载点、ETA 文本）。一次失败快照显示：stub 管线 15 秒跑完并正确渲染完成态
 * （"分析完成 · 25 个阶段 · 总耗时 0:15"），而页面在整个运行窗内未建立流订阅 → 断言
 * 120s 超时。即「负载下客户端流建立滞后 vs 15 秒 stub 运行窗」的竞态（同族抖动，先例
 * issue #312 已记录运行器级劣化）。
 *
 * 本预热 + config 的 `STUB_NODE_DELAY: '3'`（窗口 15s→30s）共同加宽可观测窗口：
 * - 预热：vite dev 的 ESM 模块图冷启动可达 20–30s（eval-ops spec 同款记录），
 *   先请求一次三个前端 URL 把图编好，测试期不再付冷启动成本；
 * - 延迟：窗口翻倍，给流订阅留出余量。
 * 断言强度不变（仍是运行中瞬态断言），只把前提（可观测窗口）做稳。
 */
const FRONTENDS = ['http://localhost:5174', 'http://localhost:5175', 'http://localhost:5176']
const BACKENDS = ['http://localhost:8001', 'http://localhost:8002', 'http://localhost:8003']

export default async function globalSetup(): Promise<void> {
  const ctx = await request.newContext()
  try {
    for (const url of BACKENDS) {
      const resp = await ctx.get(`${url}/api/health`)
      if (!resp.ok()) throw new Error(`后端预热失败 ${url}: HTTP ${resp.status()}`)
    }
    for (const url of FRONTENDS) {
      // 只取 HTML（触发 vite 入口编译）；不等待完整模块图，容错 5s
      try {
        await ctx.get(url, { timeout: 5_000 })
      } catch {
        // 预热失败不阻断（测试自身会给出真实失败证据）
      }
    }
  } finally {
    await ctx.dispose()
  }
}
