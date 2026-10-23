import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { FactorSignalCard } from '../pages/screening/FactorSignalCard'
import type { ScreeningFactorSignal } from '../types'

/** 选股页「因子信号状态」卡三态单测（delta add-factor-ic-visibility）。 */

function signal(overrides: Partial<ScreeningFactorSignal> = {}): ScreeningFactorSignal {
  return {
    selection_mode: 'fallback',
    reason: 'IC 样本不足（门槛 10）',
    min_samples: 10,
    expected_factor_mode_date: '2026-11-02',
    factors: [
      { id: 1, ic_samples: 4, eligible: false, expected_eligible_date: '2026-11-02' },
      { id: 2, ic_samples: 3, eligible: false, expected_eligible_date: '2026-11-03' },
    ],
    ...overrides,
  }
}

afterEach(cleanup)

describe('FactorSignalCard（因子信号状态）', () => {
  it('无 active 因子 → 如实空态', () => {
    render(
      <FactorSignalCard
        signal={signal({ factors: [], reason: '无 active 因子', expected_factor_mode_date: null })}
      />
    )

    expect(screen.getByTestId('factor-signal-empty').textContent).toContain('无 active 因子')
    expect(screen.queryByTestId('factor-signal-1')).toBeNull()
  })

  it('未达标 → 逐因子 x/10、未达标与预计达标日，并展示批次恢复日', () => {
    render(<FactorSignalCard signal={signal()} />)

    expect(screen.getByTestId('factor-signal-1').textContent).toContain('4/10')
    expect(screen.getByTestId('factor-signal-1').textContent).toContain('未达标')
    expect(screen.getByTestId('factor-signal-1').textContent).toContain('2026-11-02')
    expect(screen.getByTestId('factor-signal-2').textContent).toContain('3/10')
    expect(screen.getByTestId('factor-signal-reason').textContent).toContain('IC 样本不足')
    expect(screen.getByTestId('factor-signal-expected').textContent).toContain('2026-11-02')
    expect(screen.getByTestId('factor-signal-expected').textContent).toContain('按工作日推算')
  })

  it('全部达标 → 展示「因子信号已恢复」，不再展示恢复日期', () => {
    render(
      <FactorSignalCard
        signal={signal({
          selection_mode: 'factor',
          reason: '全部 active 因子 IC 样本达标',
          expected_factor_mode_date: null,
          factors: [
            { id: 1, ic_samples: 10, eligible: true, expected_eligible_date: null },
          ],
        })}
      />
    )

    expect(screen.getByTestId('factor-signal-ok').textContent).toContain('因子信号已恢复')
    expect(screen.queryByTestId('factor-signal-expected')).toBeNull()
  })
})
