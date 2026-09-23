import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import ReactECharts from 'echarts-for-react'
import type { PredictionDetail } from '../../types'
import { Button } from '../../components/ui/button'
import { cssVar } from '../../Charts'
import { PREDICTION_STATUS_LABEL } from './predictionStatus'

function fmt(v: number | null, digits = 2) {
  return v === null ? '—' : v.toFixed(digits)
}

function pct(v: number | null, digits = 1) {
  return v === null ? '—' : `${(v * 100).toFixed(digits)}%`
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span style={{ color: 'var(--text-tertiary)' }}>—</span>
  const up = value >= 0
  return <span className={`${up ? 'text-[color:var(--status-error-default)]' : 'text-[color:var(--status-success-default)]'} font-medium`}>{up ? '+' : ''}{pct(value, 2)}</span>
}

/** 判定卡信息行 */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b last:border-0" style={{ borderColor: 'var(--border-neutral-l1)' }}>
      <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <span className="text-sm" style={{ color: 'var(--text-default)' }}>{children}</span>
    </div>
  )
}

export function PredictionDetailPage({ predictionId, onBack }: { predictionId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<PredictionDetail | null>(null)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setError(false)
    try {
      const resp = await fetch(`/api/v1/track-record/predictions/${predictionId}`)
      if (!resp.ok) throw new Error(String(resp.status))
      setDetail((await resp.json()) as PredictionDetail)
    } catch {
      setError(true)
      toast.error('观点详情加载失败')
    }
  }, [predictionId])

  useEffect(() => { void load() }, [load])

  const p = detail?.prediction
  const marks = detail?.marks ?? []
  const audit = detail?.audit ?? []

  // 预测 vs 实际叠加：mark_price 序列 + entry/target 水平线
  const chartOption = {
    color: [cssVar('--chart-sky', '#228EBF'), cssVar('--chart-amber', '#CBB54C'), cssVar('--status-success-default', '#10B981')],
    tooltip: { trigger: 'axis' as const },
    legend: { bottom: 0, textStyle: { fontSize: 10 } },
    grid: { left: 52, right: 16, top: 24, bottom: 40 },
    xAxis: { type: 'category' as const, data: marks.map(m => m.mark_date), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' as const, axisLabel: { fontSize: 10 } },
    series: [
      { name: '盯市价', type: 'line' as const, data: marks.map(m => m.mark_price), showSymbol: true, symbolSize: 6, connectNulls: false },
      { name: '入场价', type: 'line' as const, data: marks.map(() => p?.entry_price ?? null), showSymbol: false, lineStyle: { type: 'dashed' as const } },
      { name: '目标价', type: 'line' as const, data: marks.map(() => p?.target_price ?? null), showSymbol: false, lineStyle: { type: 'dashed' as const } },
    ],
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8" data-testid="prediction-detail">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold" style={{ color: 'var(--text-default)' }}>
          观点详情{p ? `：${p.symbol_name ?? p.symbol}` : ''}
        </h1>
        <Button variant="ghost" size="sm" onClick={onBack} data-testid="prediction-detail-back">返回战绩</Button>
      </div>

      {error ? (
        <div className="text-sm py-16 text-center" style={{ color: 'var(--text-tertiary)' }}>数据加载失败，请刷新重试</div>
      ) : detail === null || p === undefined ? (
        <div className="py-16 text-center text-sm" style={{ color: 'var(--text-tertiary)' }}>加载中…</div>
      ) : (
        <>
          {/* 判定信息卡 */}
          <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="prediction-decision">
            <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>判定信息</div>
            <Field label="状态">{PREDICTION_STATUS_LABEL[p.status] ?? p.status}</Field>
            <Field label="方向">{p.direction}</Field>
            <Field label="置信度">{fmt(p.confidence, 2)}</Field>
            <Field label="入场价">{fmt(p.entry_price)}</Field>
            <Field label="目标价">{fmt(p.target_price)}</Field>
            <Field label="结算价">{fmt(p.exit_price)}</Field>
            <Field label="区间收益"><Delta value={p.raw_return} /></Field>
            <Field label="基准超额"><Delta value={p.excess_return} /></Field>
            <Field label="判定规则">{p.resolution_rule ?? '—'}</Field>
            <Field label="创建时间">{p.created_at?.slice(0, 19).replace('T', ' ') ?? '—'}</Field>
            <Field label="判定时间">{p.resolved_at ?? '—'}</Field>
          </div>

          {/* 预测 vs 实际叠加图（数据缺口断点不插值） */}
          <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="prediction-overlay">
            <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>盯市走势 vs 入场/目标价（虚线 = 参考价）</div>
            {marks.length >= 2 ? (
              <ReactECharts option={chartOption} style={{ height: 240 }} notMerge />
            ) : (
              <div className="py-8 text-center text-xs" style={{ color: 'var(--text-tertiary)' }}>暂无盯市序列（观点未产生每日盯市记录）</div>
            )}
          </div>

          {/* 观点快照（只读） */}
          <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="prediction-snapshot">
            <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>观点快照（写入后冻结，只读）</div>
            <pre className="text-xs whitespace-pre-wrap max-h-64 overflow-auto" style={{ color: 'var(--text-secondary)' }}>
              {typeof p.rationale_snapshot === 'string' ? p.rationale_snapshot : JSON.stringify(p.rationale_snapshot, null, 2)}
            </pre>
          </div>

          {/* 时间轴（审计轨迹） */}
          <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="prediction-audit">
            <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>事件时间轴（审计日志）</div>
            {audit.length === 0 ? (
              <div className="py-4 text-center text-xs" style={{ color: 'var(--text-tertiary)' }}>暂无审计事件</div>
            ) : (
              <div className="space-y-3">
                {audit.map(a => (
                  <div key={a.log_id} className="flex gap-3 text-xs">
                    <div className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>{a.created_at?.slice(0, 19).replace('T', ' ') ?? ''}</div>
                    <div style={{ color: 'var(--text-secondary)' }}>
                      <span className="font-medium" style={{ color: 'var(--text-default)' }}>{a.action}</span>
                      {a.old_status && a.new_status && <span>：{a.old_status} → {a.new_status}</span>}
                      {a.detail && <span className="ml-1" style={{ color: 'var(--text-tertiary)' }}>（{a.detail}）</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div data-testid="prediction-detail-disclaimer" className="mt-6 rounded-lg px-4 py-3 text-xs" style={{ background: 'rgba(250, 204, 21, 0.12)', color: 'var(--text-secondary)' }}>
            {detail.disclaimer}，投资需谨慎
          </div>
        </>
      )}
    </div>
  )
}