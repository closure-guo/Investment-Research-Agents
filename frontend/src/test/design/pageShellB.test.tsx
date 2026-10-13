import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PageShellB, type ShellTableColumn } from '../../components/shells/PageShellB'
import { SHELL_TEST_IDS } from '../../components/shells/ShellParts'
import { TABULAR_CLASS } from '../../design/format'

type Row = { id: string; symbol: string; score: number }

const ROWS: Row[] = [
  { id: 'r1', symbol: '600519', score: 88.123 },
  { id: 'r2', symbol: '000001', score: 61.5 },
]

const COLUMNS: ShellTableColumn<Row>[] = [
  { key: 'symbol', header: '标的', render: r => r.symbol },
  { key: 'score', header: '综合分', align: 'right', tabular: true, render: r => r.score.toFixed(2) },
]

function renderB(overrides: Partial<Parameters<typeof PageShellB<Row>>[0]> = {}) {
  return render(
    <PageShellB
      columns={COLUMNS}
      rows={ROWS}
      filterBar={<input data-testid="filter-child" />}
      renderExpanded={row => <div>明细 {row.symbol}</div>}
      {...overrides}
    />,
  )
}

// 列表页壳测试（unify-ui-design-system T3.2）：筛选栏 + 数据表 + 行展开。
describe('PageShellB：筛选栏 + 数据表 + 行展开', () => {
  it('渲染筛选栏与数据行', () => {
    renderB()
    expect(screen.getByTestId(SHELL_TEST_IDS.bRoot)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.filterBar)).toContainElement(screen.getByTestId('filter-child'))
    expect(screen.getByTestId(SHELL_TEST_IDS.table)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.row('r1'))).toHaveTextContent('600519')
    expect(screen.getAllByTestId(SHELL_TEST_IDS.row('r2'))).toHaveLength(1)
  })

  it('数值列右对齐 + tabular 等宽（对齐规范化）', () => {
    renderB()
    const header = screen.getByText('综合分')
    expect(header.className).toContain('text-right')
    for (const cls of TABULAR_CLASS.split(' ')) {
      expect(header.className).toContain(cls)
    }
    const cell = screen.getByText('88.12')
    expect(cell.className).toContain('text-right')
    expect(cell.className).toContain('tabular-nums')
  })

  it('行展开：点击展开入口出现详情行，再次点击收起', () => {
    renderB()
    expect(screen.queryByTestId(SHELL_TEST_IDS.rowDetail('r1'))).not.toBeInTheDocument()
    const toggle = screen.getByTestId(SHELL_TEST_IDS.rowExpand('r1'))
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(screen.getByTestId(SHELL_TEST_IDS.rowDetail('r1'))).toHaveTextContent('明细 600519')
    expect(screen.getByTestId(SHELL_TEST_IDS.rowExpand('r1'))).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.rowExpand('r1')))
    expect(screen.queryByTestId(SHELL_TEST_IDS.rowDetail('r1'))).not.toBeInTheDocument()
  })

  it('空态：统一空态（图标 + 一句说明 + 主操作按钮）', () => {
    const onEmptyAction = vi.fn()
    renderB({ rows: [], emptyMessage: '暂无候选标的', emptyActionLabel: '去选股', onEmptyAction })
    expect(screen.getByTestId(SHELL_TEST_IDS.empty)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyMessage)).toHaveTextContent('暂无候选标的')
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.emptyAction))
    expect(onEmptyAction).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId(SHELL_TEST_IDS.table)).not.toBeInTheDocument()
  })

  it('加载中：5 行骨架屏（不多不少）', () => {
    renderB({ rows: [], loading: true })
    expect(screen.getByTestId(SHELL_TEST_IDS.skeleton)).toBeInTheDocument()
    expect(screen.getAllByTestId(SHELL_TEST_IDS.skeletonRow)).toHaveLength(5)
    expect(screen.queryByTestId(SHELL_TEST_IDS.empty)).not.toBeInTheDocument()
  })

  it('未提供 renderExpanded 时不渲染展开入口（YAGNI）', () => {
    renderB({ renderExpanded: undefined })
    expect(screen.queryByTestId(SHELL_TEST_IDS.rowExpand('r1'))).not.toBeInTheDocument()
  })
})
