import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PageShellD, type ShellBatch } from '../../components/shells/PageShellD'
import { SHELL_TEST_IDS } from '../../components/shells/ShellParts'

const BATCHES: ShellBatch[] = [
  {
    id: 'b1',
    title: '2026-10-13 日批',
    subtitle: '08:30 启动',
    status: 'running',
    steps: [
      { key: 'fetch', label: '拉取行情', status: 'active' },
      { key: 'screen', label: '粗筛', status: 'running' },
      { key: 'notify', label: '推送', status: 'failed' },
    ],
  },
  {
    id: 'b2',
    title: '2026-10-12 日批',
    subtitle: '08:30 启动',
    status: 'degraded',
    steps: [{ key: 'fetch', label: '拉取行情', status: 'retired' }],
  },
]

// 运行记录壳测试（unify-ui-design-system T3.4）：批次列表 + 步骤级状态图标。
describe('PageShellD：批次列表 + 步骤级状态', () => {
  it('渲染批次列表与批次级状态徽章', () => {
    render(<PageShellD batches={BATCHES} />)
    expect(screen.getByTestId(SHELL_TEST_IDS.dRoot)).toBeInTheDocument()
    const batch = screen.getByTestId(SHELL_TEST_IDS.batch('b1'))
    expect(batch).toHaveTextContent('2026-10-13 日批')
    expect(batch).toHaveTextContent('08:30 启动')
    expect(screen.getByTestId(SHELL_TEST_IDS.batch('b2'))).toBeInTheDocument()
  })

  it('步骤级状态图标复用 StatusBadge（data-status 与步骤状态一致）', () => {
    render(<PageShellD batches={BATCHES} />)
    expect(screen.getByTestId(SHELL_TEST_IDS.steps('b1'))).toBeInTheDocument()
    const step = screen.getByTestId(SHELL_TEST_IDS.step('b1', 'screen'))
    expect(step).toHaveTextContent('粗筛')
    expect(step.querySelector('[data-testid="status-badge"]')).toHaveAttribute('data-status', 'running')
    expect(
      screen.getByTestId(SHELL_TEST_IDS.step('b1', 'notify')).querySelector('[data-testid="status-badge"]'),
    ).toHaveAttribute('data-status', 'failed')
    expect(
      screen.getByTestId(SHELL_TEST_IDS.step('b2', 'fetch')).querySelector('[data-testid="status-badge"]'),
    ).toHaveAttribute('data-status', 'retired')
  })

  it('空闲/无批次：统一空态（默认文案与主操作）', () => {
    render(<PageShellD batches={[]} emptyMessage="暂无运行批次" emptyActionLabel="手动触发日批" onEmptyAction={() => {}} />)
    expect(screen.getByTestId(SHELL_TEST_IDS.empty)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyMessage)).toHaveTextContent('暂无运行批次')
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyAction)).toHaveTextContent('手动触发日批')
  })

  it('加载中：骨架屏', () => {
    render(<PageShellD batches={[]} loading />)
    expect(screen.getByTestId(SHELL_TEST_IDS.skeleton)).toBeInTheDocument()
    expect(screen.getAllByTestId(SHELL_TEST_IDS.skeletonRow)).toHaveLength(5)
  })
})
