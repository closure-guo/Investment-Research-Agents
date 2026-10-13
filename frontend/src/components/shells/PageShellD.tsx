import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'
import { StatusBadge } from '../StatusBadge'
import type { StatusKey } from '../../design/tokens'
import { SHELL_TEST_IDS, ShellEmptyState, ShellSkeletonRows } from './ShellParts'

/**
 * PageShellD：运行记录页壳（unify-ui-design-system T3.4，spec: frontend delta）。
 *
 * 布局：批次列表 + 步骤级状态图标。步骤状态复用 StatusBadge（五态枚举），
 * 禁止自绘状态样式；批次级状态同样用 StatusBadge 展示。
 */
export interface ShellStepStatus {
  key: string
  /** 步骤名（作为 StatusBadge 文案） */
  label: ReactNode
  status: StatusKey
}

export interface ShellBatch {
  id: string
  title: ReactNode
  subtitle?: ReactNode
  status: StatusKey
  steps: readonly ShellStepStatus[]
}

export function PageShellD({
  batches,
  loading = false,
  emptyMessage = '暂无运行批次',
  emptyIcon,
  emptyActionLabel,
  onEmptyAction,
  className,
}: {
  batches: readonly ShellBatch[]
  loading?: boolean
  emptyMessage?: ReactNode
  emptyIcon?: string
  emptyActionLabel?: string
  onEmptyAction?: () => void
  className?: string
}) {
  return (
    <div data-testid={SHELL_TEST_IDS.dRoot} className={cn('flex h-full flex-col overflow-y-auto', className)}>
      {loading ? (
        <ShellSkeletonRows />
      ) : batches.length === 0 ? (
        <ShellEmptyState icon={emptyIcon} message={emptyMessage} actionLabel={emptyActionLabel} onAction={onEmptyAction} />
      ) : (
        <div className="space-y-3 p-4">
          {batches.map(batch => (
            <div
              key={batch.id}
              data-testid={SHELL_TEST_IDS.batch(batch.id)}
              className="rounded-xl border border-border p-3"
              style={{ background: 'var(--card)' }}
            >
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">{batch.title}</div>
                  {batch.subtitle && <div className="text-xs text-muted-foreground">{batch.subtitle}</div>}
                </div>
                <StatusBadge status={batch.status} />
              </div>
              <div data-testid={SHELL_TEST_IDS.steps(batch.id)} className="mt-2.5 flex flex-wrap items-center gap-3">
                {batch.steps.map(step => (
                  <span
                    key={step.key}
                    data-testid={SHELL_TEST_IDS.step(batch.id, step.key)}
                    className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                  >
                    <StatusBadge status={step.status} label={step.label} />
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
