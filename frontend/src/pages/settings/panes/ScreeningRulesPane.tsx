// ScreeningRulesPane 选股规则分区（delta add-screening-rules-config-ui）
// 三项规则运行时配置（file > env > default）；成交额以万元呈现/录入，
// 与后端元口径换算 1 万元 = 10^4 元；保存 PUT 三项整体提交，下一批次生效。
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../../../components/ui/button'

interface RuleItem {
  value: number
  source: 'file' | 'env' | 'default'
  default: number
  min: number
  max: number
  env_value?: number | null
}

interface ScreeningRules {
  min_trading_days: RuleItem
  min_avg_amount_20d: RuleItem
  l2_top_n: RuleItem
  file_status?: string
  updated_at?: string | null
}

const RULES_URL = '/api/settings/screening-rules'
// 成交额前端区间以万元定义（后端元区间 1e6–1e9 ÷ 1e4）
const AMOUNT_WAN_MIN = 100
const AMOUNT_WAN_MAX = 100_000
// 万元 → 元精确换算（整数万元 × 10^4 无舍入）
const WAN_TO_YUAN = 10_000

const SOURCE_LABEL: Record<string, string> = {
  file: '配置文件',
  env: '环境变量',
  default: '默认值',
}

interface RuleFieldProps {
  label: string
  hint: string
  item: RuleItem
  value: string
  onChange: (value: string) => void
  testId: string
}

function RuleField({ label, hint, item, value, onChange, testId }: RuleFieldProps) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
        <span>{label}（{item.min.toLocaleString()}–{item.max.toLocaleString()}）</span>
        <input
          type="number"
          inputMode="numeric"
          min={item.min}
          max={item.max}
          value={value}
          data-testid={testId}
          onChange={(e) => onChange(e.target.value)}
          className="w-32 rounded-md border border-border px-2 py-1 text-sm"
          style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)' }}
        />
      </label>
      <span
        data-testid={`${testId}-source`}
        className="rounded px-1.5 py-0.5 text-[11px]"
        style={{ background: 'var(--bg-overlay-l2)', color: 'var(--text-tertiary)' }}
      >
        {SOURCE_LABEL[item.source] ?? item.source}
      </span>
      <p className="text-[11px] w-full" style={{ color: 'var(--text-tertiary)' }}>{hint}</p>
    </div>
  )
}

export function ScreeningRulesPane() {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [rules, setRules] = useState<ScreeningRules | null>(null)
  const [minTradingDays, setMinTradingDays] = useState('')
  const [minAvgAmountWan, setMinAvgAmountWan] = useState('')
  const [l2TopN, setL2TopN] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const loadRules = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await fetch(RULES_URL)
      if (!resp.ok) throw new Error(`加载失败（${resp.status}）`)
      const body = (await resp.json()) as ScreeningRules
      setRules(body)
      setMinTradingDays(String(body.min_trading_days.value))
      // 元 → 万元展示；不能整除时保留小数（如 env 注入 5000.5 万）
      setMinAvgAmountWan(String(body.min_avg_amount_20d.value / WAN_TO_YUAN))
      setL2TopN(String(body.l2_top_n.value))
      setSaved(false)
      setSaveError(null)
      setStatus('ready')
    } catch {
      setStatus('error')
    }
  }, [])

  useEffect(() => { void loadRules() }, [loadRules])

  const save = useCallback(async () => {
    setSaved(false)
    setSaveError(null)
    // 前端拦截：整数性与区间（MUST NOT 发请求）
    const amountWan = Number(minAvgAmountWan)
    const tradingDays = Number(minTradingDays)
    const topN = Number(l2TopN)
    if (!Number.isInteger(tradingDays) || tradingDays < 1 || tradingDays > 250) {
      setSaveError(`上市天数下限须为 1–250 的整数`)
      return
    }
    if (!Number.isInteger(amountWan) || amountWan < AMOUNT_WAN_MIN || amountWan > AMOUNT_WAN_MAX) {
      setSaveError(`日均成交额下限须为 ${AMOUNT_WAN_MIN}–${AMOUNT_WAN_MAX} 的整数（万元）`)
      return
    }
    if (!Number.isInteger(topN) || topN < 1 || topN > 500) {
      setSaveError(`L2 候选 Top-N 须为 1–500 的整数`)
      return
    }
    setSaving(true)
    try {
      const resp = await fetch(RULES_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_trading_days: tradingDays,
          min_avg_amount_20d: amountWan * WAN_TO_YUAN,
          l2_top_n: topN,
        }),
      })
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => null)) as { detail?: string } | null
        throw new Error(detail?.detail ?? `保存失败（${resp.status}）`)
      }
      const body = (await resp.json()) as ScreeningRules
      setRules(body)
      setMinTradingDays(String(body.min_trading_days.value))
      setMinAvgAmountWan(String(body.min_avg_amount_20d.value / WAN_TO_YUAN))
      setL2TopN(String(body.l2_top_n.value))
      setSaved(true)
      toast.success('选股规则已保存')
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }, [minAvgAmountWan, minTradingDays, l2TopN])

  return (
    <div data-testid="screening-rules-pane" className="space-y-6">
      {status === 'loading' && (
        <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>选股规则加载中…</p>
      )}

      {status === 'error' && (
        <div
          data-testid="rules-load-error"
          className="flex items-center gap-2 text-xs"
          style={{ color: 'var(--status-error-default)' }}
        >
          <span>选股规则加载失败</span>
          <Button size="sm" variant="outline" data-testid="rules-retry" onClick={() => void loadRules()}>
            重试
          </Button>
        </div>
      )}

      {status === 'ready' && rules && (
        <div className="rounded-xl p-4 space-y-4" style={{ background: 'var(--bg-overlay-l1)' }}>
          <div>
            <h3 className="text-sm font-medium">选股规则</h3>
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              作用于选股日批 L1 硬过滤与 L2 Top-N 截断；下一次选股日批或手动补跑开始时生效，运行中批次不受影响。
            </p>
          </div>

          <RuleField
            label="上市天数下限（交易日）"
            hint="上市不足该交易日数的次新股在 L1 剔除（原因码 IPO_RECENT）"
            item={rules.min_trading_days}
            value={minTradingDays}
            onChange={(v) => { setMinTradingDays(v); setSaved(false); setSaveError(null) }}
            testId="rules-input-min-trading-days"
          />
          <RuleField
            label="近 20 日日均成交额下限（万元）"
            hint="低于该流动性门槛在 L1 剔除（原因码 ILLIQUID）；后端按元存储（1 万元 = 10⁴ 元）"
            item={{ ...rules.min_avg_amount_20d, min: AMOUNT_WAN_MIN, max: AMOUNT_WAN_MAX }}
            value={minAvgAmountWan}
            onChange={(v) => { setMinAvgAmountWan(v); setSaved(false); setSaveError(null) }}
            testId="rules-input-min-avg-amount-wan"
          />
          <RuleField
            label="L2 候选 Top-N"
            hint="因子打分排序后保留的候选数量（全量名单仍完整落库供在榜统计）"
            item={rules.l2_top_n}
            value={l2TopN}
            onChange={(v) => { setL2TopN(v); setSaved(false); setSaveError(null) }}
            testId="rules-input-l2-top-n"
          />

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" data-testid="rules-save" disabled={saving} onClick={() => void save()}>
              {saving ? '保存中…' : '保存'}
            </Button>
            {saved && (
              <span
                data-testid="rules-saved"
                className="text-xs"
                style={{ color: 'var(--status-success-default)' }}
              >
                已保存
              </span>
            )}
            {saveError !== null && (
              <span
                data-testid="rules-save-error"
                className="text-xs"
                style={{ color: 'var(--status-error-default)' }}
              >
                {saveError}
              </span>
            )}
          </div>

          {(rules.file_status === 'corrupt' || rules.file_status === 'invalid') && (
            <p className="text-[11px]" style={{ color: 'var(--status-warning-default, var(--text-tertiary))' }}>
              配置文件异常（{rules.file_status === 'corrupt' ? '不可解析' : '字段非法'}），
              异常项已回退到环境变量或默认值。
            </p>
          )}
        </div>
      )}
    </div>
  )
}
