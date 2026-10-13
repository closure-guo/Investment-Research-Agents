import { useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { cn } from '../../lib/utils'
import { useRowEntrance } from '../../design/motion'
import { TABULAR_CLASS } from '../../design/format'
import { SHELL_TEST_IDS, ShellEmptyState, ShellSkeletonRows } from './ShellParts'

/**
 * PageShellB：列表页壳（unify-ui-design-system T3.2，spec: frontend delta）。
 *
 * 布局：筛选栏 + 数据表（数值列右对齐 tabular-nums）+ 行展开详情。
 * - 空态：统一空态（图标 + 一句说明 + 主操作按钮）；
 * - 加载中：5 行骨架屏；
 * - 行入场：design/motion.ts useRowEntrance（reduced-motion 下无动画）。
 */
export interface ShellTableColumn<T> {
  key: string
  header: ReactNode
  /** 数值列建议 align='right' */
  align?: 'left' | 'right'
  /** 数值列：tabular-nums + font-mono 等宽字形 */
  tabular?: boolean
  render: (row: T) => ReactNode
}

interface ShellRowProps<T extends { id: string }> {
  row: T
  index: number
  columns: readonly ShellTableColumn<T>[]
  expanded: boolean
  onToggle: (id: string) => void
  renderExpanded?: (row: T) => ReactNode
  expandLabel: string
}

function ShellDataRow<T extends { id: string }>({
  row,
  index,
  columns,
  expanded,
  onToggle,
  renderExpanded,
  expandLabel,
}: ShellRowProps<T>) {
  // hook 必须在每个行组件实例内调用（不能在 map 回调里）
  const entrance = useRowEntrance(index)
  const numericClass = (col: ShellTableColumn<T>) =>
    cn(col.align === 'right' && 'text-right', col.tabular && TABULAR_CLASS)

  return (
    <>
      <motion.tr
        data-testid={SHELL_TEST_IDS.row(row.id)}
        className="border-t"
        style={{ borderColor: 'var(--border)' }}
        {...entrance}
      >
        {columns.map(col => (
          <td key={col.key} className={cn('px-3 py-2', numericClass(col))}>
            {col.render(row)}
          </td>
        ))}
        {renderExpanded && (
          <td className="px-3 py-2 text-right">
            <button
              type="button"
              data-testid={SHELL_TEST_IDS.rowExpand(row.id)}
              aria-expanded={expanded}
              onClick={() => onToggle(row.id)}
              className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground motion-reduce:transition-none"
            >
              {expandLabel}
            </button>
          </td>
        )}
      </motion.tr>
      {renderExpanded && expanded && (
        <tr data-testid={SHELL_TEST_IDS.rowDetail(row.id)} className="border-t" style={{ borderColor: 'var(--border)' }}>
          <td colSpan={columns.length + 1} className="px-3 py-3">
            {renderExpanded(row)}
          </td>
        </tr>
      )}
    </>
  )
}

export function PageShellB<T extends { id: string }>({
  columns,
  rows,
  filterBar,
  loading = false,
  emptyMessage = '暂无数据记录',
  emptyIcon,
  emptyActionLabel,
  onEmptyAction,
  renderExpanded,
  expandLabel = '详情',
  className,
}: {
  columns: readonly ShellTableColumn<T>[]
  rows: readonly T[]
  /** 筛选栏插槽 */
  filterBar?: ReactNode
  loading?: boolean
  emptyMessage?: ReactNode
  emptyIcon?: string
  emptyActionLabel?: string
  onEmptyAction?: () => void
  /** 提供时启用行展开（点击展开入口显示详情行） */
  renderExpanded?: (row: T) => ReactNode
  expandLabel?: string
  className?: string
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  return (
    <div data-testid={SHELL_TEST_IDS.bRoot} className={cn('flex h-full flex-col', className)}>
      {filterBar && (
        <div data-testid={SHELL_TEST_IDS.filterBar} className="shrink-0 border-b border-border p-3">
          {filterBar}
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <ShellSkeletonRows />
        ) : rows.length === 0 ? (
          <ShellEmptyState icon={emptyIcon} message={emptyMessage} actionLabel={emptyActionLabel} onAction={onEmptyAction} />
        ) : (
          <table data-testid={SHELL_TEST_IDS.table} className="w-full text-xs">
            <thead>
              <tr className="text-left" style={{ color: 'var(--text-tertiary)' }}>
                {columns.map(col => (
                  <th
                    key={col.key}
                    className={cn(
                      'px-3 py-2 font-medium',
                      col.align === 'right' && 'text-right',
                      col.tabular && TABULAR_CLASS,
                    )}
                  >
                    {col.header}
                  </th>
                ))}
                {renderExpanded && <th className="w-16 px-3 py-2 font-medium text-right">{expandLabel}</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <ShellDataRow
                  key={row.id}
                  row={row}
                  index={index}
                  columns={columns}
                  expanded={expandedId === row.id}
                  onToggle={id => setExpandedId(prev => (prev === id ? null : id))}
                  renderExpanded={renderExpanded}
                  expandLabel={expandLabel}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
