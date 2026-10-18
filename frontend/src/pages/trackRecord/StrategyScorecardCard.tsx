import { useEffect, useState, type ReactNode } from 'react'

import type { ScorecardFamily, ScorecardReason, ScorecardsResponse } from '../../types'

// 策略族成绩单区块（delta add-strategy-family-scorecards）：注册表全族四槽只读读数。
// 载荷键名权威源 src/finance_agent/outcome/scorecards.py::build_family_payload——
// family_id/name/as_of/expectancy(+_reason)/max_drawdown(+_reason)/capacity(+_reason)/
// extreme_samples/win_rate/caliber_note。
// 纪律：槽位 null → 原因码文案，MUST NOT 折算 0 或留白；族无样本 → 空态（与错误态、
// 样本不足态文案互斥）；胜率类字段仅作四槽之后的随行次要展示，不占主位。
// 状态色沿用 Tailwind 语义色 / CSS 变量，不扩展设计 tokens（本区块不需要三态徽章）。

// 原因码 → 文案（后端具名常量表：insufficient_sample / insufficient_history /
// no_liquidity_data / no_samples / no_loader）；未知码如实兜底「数据缺失」。
const REASON_LABEL: Record<string, string> = {
  insufficient_sample: '样本不足',
  insufficient_history: '结算日不足',
  no_liquidity_data: '成交额数据缺失',
  no_samples: '暂无样本',
  no_loader: '数据源未接入',
}

function reasonText(reason: ScorecardReason | null | undefined): string {
  if (reason === null || reason === undefined) return '数据缺失'
  return REASON_LABEL[reason] ?? '数据缺失'
}

// 族空数据判据：无任何样本（no_samples）或数据源未接线（no_loader）——两者均为
// 全槽 null 的空壳语义（后端 build_family_payload + get_scorecards 契约）。
function isEmptyFamily(family: ScorecardFamily): boolean {
  return (
    family.expectancy === null &&
    (family.expectancy_reason === 'no_samples' || family.expectancy_reason === 'no_loader')
  )
}

// 带符号百分比（+1.20% / -18.00%）；收益类读数统一 2 位小数
function pct(value: number): string {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

// 千分位分组（不依赖 locale，保证渲染确定性）
function groupThousands(value: number): string {
  const digits = Math.round(Math.abs(value)).toString()
  let out = ''
  for (let i = 0; i < digits.length; i += 1) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ','
    out += digits[i]
  }
  return `${value < 0 ? '-' : ''}${out}`
}

function yuan(value: number): string {
  return `¥${groupThousands(value)}`
}

// 四槽卡片外壳（标签 + 主值 + 随行脚注）：统一槽位结构，避免各槽样式漂移
function Slot({ testId, label, children }: { testId: string; label: string; children: ReactNode }) {
  return (
    <div data-testid={testId} className="rounded-lg p-3" style={{ background: 'var(--bg-overlay-l2)' }}>
      <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{label}</div>
      {children}
    </div>
  )
}

function SlotReason({ reason }: { reason: ScorecardReason | null | undefined }) {
  return (
    <div className="mt-1 text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
      {reasonText(reason)}
    </div>
  )
}

export function StrategyScorecardCard() {
  const [data, setData] = useState<ScorecardsResponse | null>(null)
  const [failed, setFailed] = useState(false)
  // 族切换为本地 state（id 而非下标：载荷刷新后下标会漂移）
  const [familyId, setFamilyId] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/scorecards')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: ScorecardsResponse) => {
        // 畸形 200 体守卫（代理错误页等）：缺 families 直通 setData 会在渲染期
        // 对 undefined 取 length 抛错炸整页 → 在 fetch 边界拦下走错误态
        if (!d || !Array.isArray(d.families)) {
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
  }, [])

  const families = data?.families ?? []
  // 选中族：显式选择优先；id 不存在（载荷变化/首帧）时回落首族（注册表序）
  const selected = families.find(f => f.family_id === familyId) ?? families[0] ?? null

  return (
    <div
      data-testid="strategy-scorecard-card"
      className="rounded-xl p-4 mb-6"
      style={{ background: 'var(--bg-overlay-l1)' }}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-1 mb-2">
        <div className="text-sm font-medium">策略族成绩单</div>
        <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
          按族注册表口径分列;样本不足的槽位如实标注,不折算 0
        </div>
      </div>

      {failed ? (
        <div data-testid="scorecard-error" className="text-xs py-2" style={{ color: 'var(--text-secondary)' }}>
          成绩单加载失败
        </div>
      ) : data === null ? (
        <div className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }}>加载中…</div>
      ) : families.length === 0 ? (
        <div data-testid="scorecard-empty" className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }}>
          暂无策略族成绩单数据
        </div>
      ) : (
        <>
          <div data-testid="scorecard-family-tabs" className="flex flex-wrap gap-1 mb-3" role="tablist">
            {families.map(f => {
              const active = f.family_id === selected?.family_id
              return (
                <button
                  key={f.family_id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  data-testid={`scorecard-family-tab-${f.family_id}`}
                  onClick={() => setFamilyId(f.family_id)}
                  className="text-xs rounded-lg px-3 py-1 border"
                  style={
                    active
                      ? { background: 'var(--bg-overlay-l2)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l2)' }
                      : { color: 'var(--text-secondary)', borderColor: 'var(--border-neutral-l1)' }
                  }
                >
                  {f.name}
                </button>
              )
            })}
          </div>

          {selected !== null && (isEmptyFamily(selected) ? (
            /* 族空数据：全槽 null 的空壳语义——展示空态文案替代四槽，
               MUST NOT 显示错误提示或 0/空白槽位冒充读数 */
            <div data-testid="scorecard-empty" className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }}>
              {reasonText(selected.expectancy_reason)}
            </div>
          ) : (
            <>
              {selected.as_of !== null && (
                <div className="text-[10px] mb-2" style={{ color: 'var(--text-tertiary)' }} data-testid="scorecard-as-of">
                  样本截至 {selected.as_of}
                </div>
              )}

              {/* 四槽主位：期望值（毛/净+样本量）/ 最大回撤 / 容量提示 / 极端样本数 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Slot testId="scorecard-expectancy" label="期望值（毛 / 净）">
                  {selected.expectancy === null ? (
                    // insufficient_sample 走专属文案槽（spec：MUST NOT 显示 0 或空白）
                    selected.expectancy_reason === 'insufficient_sample' ? (
                      <div className="mt-1 text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                        <span data-testid="scorecard-insufficient">样本不足</span>
                      </div>
                    ) : (
                      <SlotReason reason={selected.expectancy_reason} />
                    )
                  ) : (
                    <>
                      <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--text-default)' }}>
                        毛 {pct(selected.expectancy.gross)} · 净 {pct(selected.expectancy.net)}
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        样本 {selected.expectancy.n} 条
                      </div>
                    </>
                  )}
                </Slot>

                <Slot testId="scorecard-drawdown" label="最大回撤">
                  {selected.max_drawdown === null ? (
                    <SlotReason reason={selected.max_drawdown_reason} />
                  ) : (
                    <>
                      <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--status-error-default)' }}>
                        {pct(selected.max_drawdown.value)}
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        {selected.max_drawdown.n_days} 个结算日
                      </div>
                    </>
                  )}
                </Slot>

                <Slot testId="scorecard-capacity" label="容量提示">
                  {selected.capacity === null ? (
                    <SlotReason reason={selected.capacity_reason} />
                  ) : (
                    <>
                      <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--text-default)' }}>
                        建议资金上限 {yuan(selected.capacity.suggested_capital)}
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        最小标的日均成交额 {yuan(selected.capacity.min_adv20)}
                      </div>
                      {selected.capacity.excluded > 0 && (
                        <div className="text-[10px]" style={{ color: 'var(--status-warning-default)' }}>
                          {selected.capacity.excluded} 个标的缺成交额数据已剔除
                        </div>
                      )}
                    </>
                  )}
                </Slot>

                <Slot testId="scorecard-extremes" label="极端样本">
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--text-default)' }}>
                    {selected.extreme_samples.length === 0 ? '无' : `${selected.extreme_samples.length} 条`}
                  </div>
                </Slot>
              </div>

              {/* 极端样本明细表：标的 / 结算日 / 收益（无条目只留槽位「无」，不渲染空表） */}
              {selected.extreme_samples.length > 0 && (
                <table className="w-full text-xs mt-3" data-testid="scorecard-extremes-table">
                  <thead>
                    <tr style={{ color: 'var(--text-tertiary)' }}>
                      <th className="py-1 pr-2 text-left font-normal">标的</th>
                      <th className="py-1 pr-2 text-left font-normal">结算日</th>
                      <th className="py-1 text-right font-normal">收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.extreme_samples.map(s => (
                      <tr
                        key={`${s.ticker}-${s.date}`}
                        data-testid={`scorecard-extreme-row-${s.ticker}-${s.date}`}
                        className="border-t"
                        style={{ borderColor: 'var(--border-neutral-l1)' }}
                      >
                        <td className="py-1 pr-2" style={{ color: 'var(--text-default)' }}>{s.ticker}</td>
                        <td className="py-1 pr-2" style={{ color: 'var(--text-secondary)' }}>{s.date}</td>
                        <td className="py-1 text-right" style={{ color: 'var(--status-error-default)' }}>{pct(s.ret)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* 随行脚注：胜率仅展示（四槽之后，不占主位）+ 口径注释（design：必须随行展示） */}
              <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                {selected.win_rate !== null && (
                  <span data-testid="scorecard-win-rate">胜率 {(selected.win_rate * 100).toFixed(1)}%（仅展示）</span>
                )}
                <span data-testid="scorecard-caliber-note">{selected.caliber_note}</span>
              </div>
            </>
          ))}
        </>
      )}
    </div>
  )
}
