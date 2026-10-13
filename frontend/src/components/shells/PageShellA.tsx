import { useState, type ReactNode } from 'react'
import { cn } from '../../lib/utils'
import { TAB_FADE_CLASS } from '../../design/motion'
import { TABULAR_CLASS } from '../../design/format'
import { SHELL_TEST_IDS, ShellEmptyState, ShellSkeletonChart, ShellSkeletonMetrics } from './ShellParts'

/**
 * PageShellA：工作台页壳（unify-ui-design-system T3.1，spec: frontend delta）。
 *
 * 布局：顶部指标条（KPI 方块）+ 主图表插槽 + 底部 Tab 表格（内容区 150ms 淡入）。
 * 纯展示壳：所有数据/图表/Tab 内容由调用方（功能页）注入，禁止自由布局。
 */
export interface ShellMetricBlock {
  key: string
  label: ReactNode
  /** 指标数值（建议经 design/format.ts 规范化后传入） */
  value: ReactNode
  /** 次要说明（可选） */
  sub?: ReactNode
}

export interface ShellTab {
  key: string
  label: ReactNode
  content: ReactNode
}

export function PageShellA({
  metrics,
  chart,
  tabs,
  title,
  loading = false,
  empty = false,
  emptyIcon,
  emptyMessage = '暂无指标数据',
  emptyActionLabel,
  onEmptyAction,
  defaultTabKey,
  className,
}: {
  metrics: readonly ShellMetricBlock[]
  /** 主图表插槽内容（ECharts 实例等） */
  chart: ReactNode
  tabs: readonly ShellTab[]
  title?: ReactNode
  loading?: boolean
  empty?: boolean
  emptyIcon?: string
  emptyMessage?: ReactNode
  emptyActionLabel?: string
  onEmptyAction?: () => void
  /** 初始激活 Tab（默认第一个） */
  defaultTabKey?: string
  className?: string
}) {
  const [activeTabKey, setActiveTabKey] = useState<string | undefined>(defaultTabKey)
  const activeTab = tabs.find(t => t.key === activeTabKey) ?? tabs[0]

  return (
    <div data-testid={SHELL_TEST_IDS.aRoot} className={cn('flex h-full flex-col', className)}>
      {loading ? (
        <div data-testid={SHELL_TEST_IDS.skeleton} className="space-y-4 p-4">
          <ShellSkeletonMetrics count={metrics.length} />
          <ShellSkeletonChart />
        </div>
      ) : empty ? (
        <ShellEmptyState icon={emptyIcon} message={emptyMessage} actionLabel={emptyActionLabel} onAction={onEmptyAction} />
      ) : (
        <>
          <div className="flex-1 overflow-y-auto">
            {title && <div className="px-4 pt-4 text-sm font-semibold text-foreground">{title}</div>}
            <div data-testid={SHELL_TEST_IDS.metrics} className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4">
              {metrics.map(metric => (
                <div
                  key={metric.key}
                  data-testid={SHELL_TEST_IDS.metric(metric.key)}
                  className="rounded-xl border border-border p-3"
                  style={{ background: 'var(--card)' }}
                >
                  <div className="text-xs text-muted-foreground">{metric.label}</div>
                  <div className={cn('mt-1 text-lg font-semibold text-foreground', TABULAR_CLASS)}>{metric.value}</div>
                  {metric.sub && <div className="mt-0.5 text-xs text-muted-foreground">{metric.sub}</div>}
                </div>
              ))}
            </div>
            <div data-testid={SHELL_TEST_IDS.chart} className="px-4 pb-4">
              {chart}
            </div>
          </div>

          <div className="shrink-0 border-t border-border">
            <div data-testid={SHELL_TEST_IDS.nav} role="tablist" className="flex items-center gap-1 px-4 pt-3">
              {tabs.map(tab => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={tab.key === activeTab?.key}
                  data-testid={SHELL_TEST_IDS.tab(tab.key)}
                  onClick={() => setActiveTabKey(tab.key)}
                  className={cn(
                    'h-7 rounded-full px-3 text-xs transition-colors motion-reduce:transition-none',
                    tab.key === activeTab?.key
                      ? 'bg-accent font-medium text-accent-foreground'
                      : 'text-muted-foreground hover:bg-muted',
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {/* key 触发重挂载以重播 150ms 淡入；reduced-motion 下由 CSS/类名双降级 */}
            <div
              key={activeTab?.key}
              data-testid={SHELL_TEST_IDS.tabContent}
              data-tab={activeTab?.key}
              className={cn(TAB_FADE_CLASS, 'p-4')}
            >
              {activeTab?.content}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
