import { describe, it, expect } from 'vitest'
import { readDeclaredPrices } from '../../pages/trackRecord/predictionDisplay'

describe('readDeclaredPrices（#606 极性契约读侧）', () => {
  it('JSON 字符串快照：提取三价与极性', () => {
    const r = readDeclaredPrices({
      rationale_snapshot: JSON.stringify({
        declared_prices: { entry_price: 5.12, stop_loss: 4.9, target_price: 5.3971, polarity: 'exit' },
      }),
      declared_polarity: 'exit',
    })
    expect(r).toEqual({ entryPrice: 5.12, stopLoss: 4.9, targetPrice: 5.3971, polarity: 'exit' })
  })

  it('极性：服务端非空声明优先，null 回退快照解析（解析边缘兜底）', () => {
    const serverWins = readDeclaredPrices({
      rationale_snapshot: { declared_prices: { polarity: 'short' } },
      declared_polarity: 'exit',
    })
    expect(serverWins?.polarity).toBe('exit')
    const snapshotFallback = readDeclaredPrices({
      rationale_snapshot: { declared_prices: { stop_loss: 1, polarity: 'exit' } },
      declared_polarity: null,
    })
    expect(snapshotFallback?.polarity).toBe('exit')
    const neither = readDeclaredPrices({
      rationale_snapshot: { declared_prices: { stop_loss: 1 } },
      declared_polarity: null,
    })
    expect(neither?.polarity).toBeNull()
  })

  it('无快照 / 非法 JSON / 非 dict declared_prices → null（读侧宽松）', () => {
    expect(readDeclaredPrices({ rationale_snapshot: undefined })).toBeNull()
    expect(readDeclaredPrices({ rationale_snapshot: 'not-json{' })).toBeNull()
    expect(readDeclaredPrices({ rationale_snapshot: { declared_prices: 'junk' } })).toBeNull()
  })
})
