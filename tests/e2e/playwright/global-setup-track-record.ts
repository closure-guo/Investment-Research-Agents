import { request } from '@playwright/test'

/**
 * 战绩页套件全量复位（2026-10-13，E2E 修复）。
 *
 * 背景：本套件多个 spec 假设「库内只有自己造的行」（如 track-record-pred-tabs 的
 * 空态前提、duplicates 的行唯一断言）。CI 每次全新起库天然干净；**本地重跑**时上一轮
 * 种子残留会让这些断言误红（实测残留 49 条 predictions → 6 例失败）。
 *
 * 机制：Playwright 1.62 实测 webServer 先于 globalSetup 启动（探针
 * `serverUpDuringGlobalSetup: true`），故此处可直接经 TESTING 端点复位。
 * 复位语义见 `/api/test/seed` 的 `track_record_reset`（五张造数面表 + paper_equity）。
 */
const BASE = 'http://127.0.0.1:8004'

export default async function globalSetup(): Promise<void> {
  const ctx = await request.newContext()
  try {
    const resp = await ctx.post(`${BASE}/api/test/seed`, {
      data: { track_record_reset: true },
    })
    if (!resp.ok()) {
      throw new Error(
        `track-record 全量复位失败：HTTP ${resp.status()} ${await resp.text()}`,
      )
    }
  } finally {
    await ctx.dispose()
  }
}
