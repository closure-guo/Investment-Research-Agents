import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'

/**
 * 页面壳共享子部件（unify-ui-design-system T3.1~T3.4）。
 *
 * 统一空态：图标 + 一句说明 + 主操作按钮（spec 钉死三要素）；
 * 骨架屏：列表页 5 行 / 工作台页指标方块 + 图表矩形。
 *
 * ── testid 契约（全部 page-shell-* 前缀，供后续功能页 E2E 复用）──
 * 共享：
 *   page-shell-empty           统一空态容器
 *   page-shell-empty-icon      空态图标
 *   page-shell-empty-message   空态一句说明
 *   page-shell-empty-action    空态主操作按钮（提供 label 时渲染）
 *   page-shell-skeleton        骨架屏容器
 *   page-shell-skeleton-row    列表骨架行（B/D，5 行）
 *   page-shell-skeleton-metric 指标骨架方块（A，数量同 metrics）
 *   page-shell-skeleton-chart  图表骨架矩形（A）
 * PageShellA（工作台）：
 *   page-shell-a / page-shell-metrics / page-shell-metric-{key}
 *   page-shell-chart / page-shell-nav / page-shell-tab-{key} / page-shell-tab-content
 * PageShellB（列表）：
 *   page-shell-b / page-shell-filter-bar / page-shell-table
 *   page-shell-row-{id} / page-shell-row-expand-{id} / page-shell-row-detail-{id}
 * PageShellC（审批队列）：
 *   page-shell-c / page-shell-list / page-shell-list-item-{id}
 *   page-shell-detail / page-shell-confirm / page-shell-reject
 * PageShellD（运行记录）：
 *   page-shell-d / page-shell-batch-{id} / page-shell-steps-{batchId}
 *   page-shell-step-{batchId}-{stepKey}（内含 status-badge）
 */
export const SHELL_TEST_IDS = {
  // 共享
  empty: 'page-shell-empty',
  emptyIcon: 'page-shell-empty-icon',
  emptyMessage: 'page-shell-empty-message',
  emptyAction: 'page-shell-empty-action',
  skeleton: 'page-shell-skeleton',
  skeletonRow: 'page-shell-skeleton-row',
  skeletonMetric: 'page-shell-skeleton-metric',
  skeletonChart: 'page-shell-skeleton-chart',
  // A 工作台
  aRoot: 'page-shell-a',
  metrics: 'page-shell-metrics',
  metric: (key: string) => `page-shell-metric-${key}`,
  chart: 'page-shell-chart',
  nav: 'page-shell-nav',
  tab: (key: string) => `page-shell-tab-${key}`,
  tabContent: 'page-shell-tab-content',
  // B 列表
  bRoot: 'page-shell-b',
  filterBar: 'page-shell-filter-bar',
  table: 'page-shell-table',
  row: (id: string) => `page-shell-row-${id}`,
  rowExpand: (id: string) => `page-shell-row-expand-${id}`,
  rowDetail: (id: string) => `page-shell-row-detail-${id}`,
  // C 审批队列
  cRoot: 'page-shell-c',
  list: 'page-shell-list',
  listItem: (id: string) => `page-shell-list-item-${id}`,
  detail: 'page-shell-detail',
  confirm: 'page-shell-confirm',
  reject: 'page-shell-reject',
  // D 运行记录
  dRoot: 'page-shell-d',
  batch: (id: string) => `page-shell-batch-${id}`,
  steps: (batchId: string) => `page-shell-steps-${batchId}`,
  step: (batchId: string, stepKey: string) => `page-shell-step-${batchId}-${stepKey}`,
} as const

/**
 * 统一空态：图标 + 一句说明 + 主操作按钮（spec 钉死三要素）。
 *
 * actionLabel 与 onAction 必须成对提供——三要素是 spec 契约，调用方不应省略主操作；
 * 未提供时仅渲染图标 + 说明（降级为只读空态，供无操作场景使用）。
 */
export function ShellEmptyState({
  icon = 'fa-inbox',
  message,
  actionLabel,
  onAction,
}: {
  icon?: string
  message: ReactNode
  actionLabel?: string
  onAction?: () => void
}) {
  return (
    <div data-testid={SHELL_TEST_IDS.empty} className="flex flex-col items-center justify-center gap-3 py-16">
      <i
        data-testid={SHELL_TEST_IDS.emptyIcon}
        className={cn('fas text-3xl', icon)}
        aria-hidden="true"
        style={{ color: 'var(--text-tertiary)' }}
      />
      <p data-testid={SHELL_TEST_IDS.emptyMessage} className="text-sm text-muted-foreground">
        {message}
      </p>
      {actionLabel && (
        <Button data-testid={SHELL_TEST_IDS.emptyAction} size="sm" onClick={onAction} className="motion-reduce:transition-none">
          {actionLabel}
        </Button>
      )}
    </div>
  )
}

/** 骨架条（背景取 --muted，pulse 动画随 reduced-motion 禁用） */
export function ShellSkeletonBar({ className, testId }: { className?: string; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className={cn('animate-pulse rounded motion-reduce:animate-none', className)}
      style={{ background: 'var(--muted)' }}
    />
  )
}

/** 列表骨架屏：5 行（spec：列表页固定 5 行） */
export function ShellSkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div data-testid={SHELL_TEST_IDS.skeleton} className="px-4 py-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} data-testid={SHELL_TEST_IDS.skeletonRow} className="flex items-center gap-3 border-b border-border px-4 py-3">
          <ShellSkeletonBar className="h-4 w-1/4" />
          <ShellSkeletonBar className="h-4 w-1/6" />
          <ShellSkeletonBar className="h-4 flex-1" />
        </div>
      ))}
    </div>
  )
}

/** 工作台骨架屏：指标方块（数量同 metrics） */
export function ShellSkeletonMetrics({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {Array.from({ length: Math.max(count, 1) }, (_, i) => (
        <ShellSkeletonBar key={i} className="h-20" testId={SHELL_TEST_IDS.skeletonMetric} />
      ))}
    </div>
  )
}

/** 工作台骨架屏：图表矩形 */
export function ShellSkeletonChart() {
  return <ShellSkeletonBar className="h-64 w-full rounded-xl" testId={SHELL_TEST_IDS.skeletonChart} />
}
