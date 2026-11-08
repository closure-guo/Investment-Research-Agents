import { useEffect, useState } from 'react'

import type { IndexCompareResponse } from '../../types'
import type { TrackTimeSpan } from '../../lib/trackPrefs'

// 跑赢指数对比卡片(add-index-performance-compare):组合区间收益 vs 主要指数同期收益。
// span 随战绩页跨度偏好传入;指数缺数灰显,beat 直读比较(与判定链 ±2% 带无关)。
export function IndexCompareCard({ span }: { span: TrackTimeSpan }) {
  const [data, setData] = useState<IndexCompareResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    // span 切换即换窗口:旧窗口数据必须立刻清除,防止新窗口拉取失败时
    // 旧摘要/对比条残留造成跨窗口混排(Task 8 审查修复)
    setData(null)
    setFailed(false)
    fetch(`/api/v1/track-record/index-compare?span=${span}`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: IndexCompareResponse) => {
        // 畸形 200 体守卫(代理错误页等):缺 indices 直通 setData 会在渲染期
        // data.indices.every 抛 TypeError 炸整页 → 在 fetch 边界拦下走失败态
        if (!d || !Array.isArray(d.indices)) {
          if (alive) setFailed(true)
          return
        }
        if (alive) setData(d)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [span])

  return (
    <div
      data-testid="index-compare-card"
      className="rounded-lg border p-4"
      style={{ borderColor: 'var(--border-neutral-l1)' }}
    >
      <div className="mb-2 text-sm font-medium">跑赢指数对比</div>
      {failed && <div className="text-xs text-[var(--text-secondary)]">对比数据加载失败</div>}
      {/* M=0 空态(spec:agent_return 有值但窗口内无指数数据时,不渲染摘要与对比条) */}
      {data && data.agent_return !== null && data.indices.every(i => i.beat === null) && (
        <div data-testid="index-compare-empty" className="text-xs text-[var(--text-secondary)]">
          窗口内暂无指数数据,暂无法对比
        </div>
      )}
      {data && data.agent_return !== null && data.indices.some(i => i.beat !== null) && (
        <>
          <div data-testid="index-compare-summary" className="mb-3 text-lg font-semibold">
            跑赢 {data.indices.filter(i => i.beat === true).length}/{data.indices.filter(i => i.beat !== null).length} 个指数
          </div>
          <div className="flex flex-col gap-1.5">
            {/* 组合条置顶(对比基准线):无 beat 标记,不参与降序 */}
            <div data-testid="index-compare-row-agent" className="flex items-center gap-2 text-sm font-semibold">
              <span className="w-20 shrink-0">本组合</span>
              <span className="flex-1">{(data.agent_return * 100).toFixed(2)}%</span>
              <span className="text-xs text-[var(--text-secondary)]">对比基准线</span>
            </div>
            {[...data.indices]
              .sort((a, b) => (b.return ?? -Infinity) - (a.return ?? -Infinity))
              .map(i => (
                <div key={i.code} data-testid={`index-compare-row-${i.code}`} className="flex items-center gap-2 text-sm">
                  <span className="w-20 shrink-0">{i.name}</span>
                  {i.return === null ? (
                    <span className="text-[var(--text-secondary)]">无数据</span>
                  ) : (
                    <>
                      <span className="flex-1">
                        {(i.return * 100).toFixed(2)}%
                        {/* effective_start_date 可 null:仅在非空且 ≠ 窗口首日时标注 */}
                        {i.effective_start_date && i.effective_start_date !== data.window.start && (
                          <span className="ml-1 text-xs text-[var(--text-secondary)]">
                            (自 {i.effective_start_date} 起算)
                          </span>
                        )}
                      </span>
                      <span style={{ color: i.beat ? 'var(--status-success-default)' : 'var(--status-error-default)' }}>
                        {i.beat ? '↑ 跑赢' : '↓ 跑输'}
                      </span>
                    </>
                  )}
                </div>
              ))}
          </div>
        </>
      )}
      {data && data.agent_return === null && (
        <div data-testid="index-compare-empty" className="text-xs text-[var(--text-secondary)]">
          净值数据积累中,暂无法对比
        </div>
      )}
      {/* 口径披露(add-track-record-caliber-disclosure):净值只聚合 long/short 观点,
          观望/持有不进组合。不写清这一条,读者会把「空头账本」当成全仓组合来读。 */}
      {data && data.agent_return !== null && data.caliber && (
        <div
          data-testid="index-compare-caliber"
          className="mt-3 border-t pt-2 text-xs text-[var(--text-secondary)]"
          style={{ borderColor: 'var(--border-neutral-l1)' }}
        >
          口径:仅统计买入/卖出观点(观望/持有不计入组合仓位);{data.caliber.nav_points} 个交易日 ·
          累计多 {data.caliber.long_views} / 空 {data.caliber.short_views} · 期内已结算{' '}
          {data.caliber.settled_count} 笔
          <div className="mt-1">
            卖出观点按做空记账,属评分语义;A 股该路径不建模可执行做空,故本曲线不等于实盘账户净值。
          </div>
        </div>
      )}
      <div className="mt-2 text-xs text-[var(--text-secondary)]">{data?.disclaimer}</div>
    </div>
  )
}
