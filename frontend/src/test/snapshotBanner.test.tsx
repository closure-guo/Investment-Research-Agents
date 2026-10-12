// add-market-snapshot T9 组件测试：
// SnapshotBanner 挂载即轮询 GET /api/snapshot/status，仅 stale && message 时
// 渲染全局横幅（文案由后端逐字给定）；fetch 失败静默不渲染、不崩溃。
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, act } from '@testing-library/react'
import { SnapshotBanner } from '../SnapshotBanner'

// D5 逐字文案（后端 compute_snapshot_status 给定，前端不得改写）
const STALE_MESSAGE = '行情快照未更新（数据日期: 2026-10-09）'

function okFetch(payload: object) {
  return vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SnapshotBanner（add-market-snapshot）', () => {
  it('stale=true 且 message 非空 → 横幅可见并逐字渲染后端文案', async () => {
    vi.stubGlobal(
      'fetch',
      okFetch({
        latest_date: '2026-10-09',
        stale: true,
        message: STALE_MESSAGE,
        last_error: 'boom',
        last_error_date: '2026-10-13',
      }),
    )
    render(<SnapshotBanner />)
    const banner = await screen.findByTestId('snapshot-stale-banner')
    expect(banner).toHaveTextContent(STALE_MESSAGE)
  })

  it('stale=false → 不渲染横幅', async () => {
    const fetchMock = okFetch({ latest_date: '2026-10-13', stale: false, message: null })
    vi.stubGlobal('fetch', fetchMock)
    render(<SnapshotBanner />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('snapshot-stale-banner')).not.toBeInTheDocument()
  })

  it('fetch 拒绝 → 不渲染横幅且不崩溃', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error('network down')))
    vi.stubGlobal('fetch', fetchMock)
    render(<SnapshotBanner />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('snapshot-stale-banner')).not.toBeInTheDocument()
  })

  it('卸载后清理 60s 轮询定时器（不再发起请求）', async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = okFetch({ latest_date: '2026-10-09', stale: true, message: STALE_MESSAGE })
      vi.stubGlobal('fetch', fetchMock)
      const { unmount } = render(<SnapshotBanner />)
      // 冲掉挂载时首轮 fetch 的微任务（假定时器下）
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)

      unmount()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(120_000)
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
