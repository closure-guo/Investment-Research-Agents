import type { ScreeningFactorSignal } from '../../types'

/**
 * 选股页「因子信号状态」卡片（delta add-factor-ic-visibility）。
 *
 * 三态：无 active 因子 / 未达标（逐因子 x/10 + 预计恢复日）/ 已达标。
 * 数据全部取自池接口 `factor_signal`，前端不自行推算日期。
 */
export function FactorSignalCard({ signal }: { signal: ScreeningFactorSignal }) {
  const factors = signal.factors
  const allEligible = factors.length > 0 && factors.every(factor => factor.eligible)

  return (
    <div
      data-testid="factor-signal-card"
      className="rounded-lg border px-3 py-2 text-xs space-y-1"
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="font-medium text-foreground">因子信号状态</div>

      {factors.length === 0 ? (
        <div data-testid="factor-signal-empty" className="text-muted-foreground">
          {signal.reason}
        </div>
      ) : (
        <>
          <div data-testid="factor-signal-reason" className="text-muted-foreground">
            {signal.reason}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {factors.map(factor => (
              <span key={factor.id} data-testid={`factor-signal-${factor.id}`} className="tabular-nums">
                因子 {factor.id}：{factor.ic_samples}/{signal.min_samples}
                <span className="ml-1" style={{ color: factor.eligible ? 'var(--status-success-default)' : 'var(--status-warning-default)' }}>
                  {factor.eligible ? '已达标' : '未达标'}
                </span>
                {!factor.eligible && factor.expected_eligible_date && (
                  <span className="ml-1" style={{ color: 'var(--text-tertiary)' }}>
                    （预计 {factor.expected_eligible_date}）
                  </span>
                )}
              </span>
            ))}
          </div>

          {allEligible ? (
            <div data-testid="factor-signal-ok" style={{ color: 'var(--status-success-default)' }}>
              因子信号已恢复
            </div>
          ) : (
            signal.expected_factor_mode_date && (
              <div data-testid="factor-signal-expected" style={{ color: 'var(--status-warning-default)' }}>
                预计 {signal.expected_factor_mode_date} 恢复因子打分（按工作日推算、不含节假日）
              </div>
            )
          )}
        </>
      )}
    </div>
  )
}
