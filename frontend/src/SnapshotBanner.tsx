import { useEffect, useState } from 'react'

/**
 * 行情快照新鲜度全局横幅（add-market-snapshot D5/T9）。
 *
 * 挂载时立即 + 之后每 60s 轮询 ``GET /api/snapshot/status``（该端点永不 500，
 * 异常降级 stale=false）；仅当 ``stale === true`` 且 ``message`` 非空时渲染，
 * 文案由后端逐字给定（「行情快照未更新（数据日期: YYYY-MM-DD）」），前端不改写。
 * fetch 失败时静默保留上一轮状态（宁缺勿噪），不打断轮询；卸载时清定时器。
 */

const POLL_INTERVAL_MS = 60_000

// /api/snapshot/status 载荷（只用渲染所需字段；其余字段由后端契约保证）
interface SnapshotStatus {
  stale?: boolean
  message?: string | null
}

export function SnapshotBanner() {
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const resp = await fetch('/api/snapshot/status')
        if (!resp.ok) return // 非 2xx：保留上一轮状态
        const data = (await resp.json()) as SnapshotStatus
        if (cancelled) return
        setMessage(data?.stale === true && data?.message ? String(data.message) : null)
      } catch {
        // 网络/解析失败：保留上一轮状态（首次失败即保持隐藏）
      }
    }

    void poll()
    const timer = setInterval(() => {
      void poll()
    }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (!message) return null

  return (
    <div
      data-testid="snapshot-stale-banner"
      role="alert"
      className="fixed top-16 left-1/2 -translate-x-1/2 z-[60] px-4 py-2 rounded-lg text-xs flex items-center gap-2 shadow-lg"
      style={{ background: 'var(--status-warning-default)', color: 'white' }}
    >
      <i className="fas fa-exclamation-triangle"></i>
      {message}
    </div>
  )
}
