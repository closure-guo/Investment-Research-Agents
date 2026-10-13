import { useState, type ReactNode } from 'react'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { SHELL_TEST_IDS, ShellEmptyState, ShellSkeletonRows } from './ShellParts'

/**
 * PageShellC：审批队列壳（unify-ui-design-system T3.3，spec: frontend delta）。
 *
 * 布局：左列表 + 右详情 + 底部确认/驳回操作区。
 * - 选择态：受控（selectedId）与内部态（initialSelectedId + 点击选择）双模式；
 * - 未选中时确认/驳回禁用（disabled 表达「无操作对象」，不是隐藏）；
 * - 回调控件：onSelect / onConfirm / onReject 携带条目对象；无回执时按钮仍可用
 *   （本壳不承担业务语义，仅触发）。
 */
export function PageShellC<T extends { id: string }>({
  items,
  renderItem,
  renderDetail,
  selectedId,
  initialSelectedId = null,
  onSelect,
  onConfirm,
  onReject,
  confirmLabel = '确认',
  rejectLabel = '驳回',
  loading = false,
  emptyMessage = '暂无待审批条目',
  emptyIcon,
  emptyActionLabel,
  onEmptyAction,
  className,
}: {
  items: readonly T[]
  renderItem: (item: T) => ReactNode
  renderDetail: (item: T) => ReactNode
  /** 受控选中（提供后忽略内部态） */
  selectedId?: string | null
  /** 非受控初始选中 */
  initialSelectedId?: string | null
  onSelect?: (id: string) => void
  onConfirm?: (item: T) => void
  onReject?: (item: T) => void
  confirmLabel?: string
  rejectLabel?: string
  loading?: boolean
  emptyMessage?: ReactNode
  emptyIcon?: string
  emptyActionLabel?: string
  onEmptyAction?: () => void
  className?: string
}) {
  const [internalId, setInternalId] = useState<string | null>(initialSelectedId)
  const activeId = selectedId !== undefined ? selectedId : internalId
  const selectedItem = items.find(item => item.id === activeId) ?? null

  const select = (id: string) => {
    setInternalId(id)
    onSelect?.(id)
  }

  if (loading) {
    return (
      <div data-testid={SHELL_TEST_IDS.cRoot} className={cn('h-full', className)}>
        <ShellSkeletonRows />
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div data-testid={SHELL_TEST_IDS.cRoot} className={cn('flex h-full flex-col', className)}>
        <ShellEmptyState icon={emptyIcon} message={emptyMessage} actionLabel={emptyActionLabel} onAction={onEmptyAction} />
      </div>
    )
  }

  return (
    <div data-testid={SHELL_TEST_IDS.cRoot} className={cn('grid h-full grid-cols-[280px_1fr]', className)}>
      <div data-testid={SHELL_TEST_IDS.list} className="overflow-y-auto border-r border-border">
        {items.map(item => (
          <button
            key={item.id}
            type="button"
            data-testid={SHELL_TEST_IDS.listItem(item.id)}
            data-selected={item.id === activeId}
            aria-pressed={item.id === activeId}
            onClick={() => select(item.id)}
            className={cn(
              'block w-full border-b border-border px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted motion-reduce:transition-none',
              item.id === activeId && 'bg-accent text-accent-foreground',
            )}
          >
            {renderItem(item)}
          </button>
        ))}
      </div>
      <div className="flex min-w-0 flex-col">
        <div data-testid={SHELL_TEST_IDS.detail} className="flex-1 overflow-y-auto p-4">
          {selectedItem ? (
            renderDetail(selectedItem)
          ) : (
            <p className="text-sm text-muted-foreground">从左侧列表选择一条查看详情</p>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border p-3">
          <Button
            data-testid={SHELL_TEST_IDS.reject}
            variant="outline"
            size="sm"
            disabled={!selectedItem}
            onClick={() => selectedItem && onReject?.(selectedItem)}
            className="motion-reduce:transition-none"
          >
            {rejectLabel}
          </Button>
          <Button
            data-testid={SHELL_TEST_IDS.confirm}
            size="sm"
            disabled={!selectedItem}
            onClick={() => selectedItem && onConfirm?.(selectedItem)}
            className="motion-reduce:transition-none"
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
