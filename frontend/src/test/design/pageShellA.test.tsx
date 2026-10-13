import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PageShellA } from '../../components/shells/PageShellA'
import { SHELL_TEST_IDS } from '../../components/shells/ShellParts'
import { TAB_FADE_CLASS } from '../../design/motion'

const METRICS = [
  { key: 'ret', label: '区间收益', value: '+2.31%' },
  { key: 'dd', label: '最大回撤', value: '-1.05%', sub: '近 60 日' },
]
const TABS = [
  { key: 'holdings', label: '持仓', content: <span>持仓表内容</span> },
  { key: 'trades', label: '交易', content: <span>交易记录内容</span> },
]

function renderA(overrides: Partial<Parameters<typeof PageShellA>[0]> = {}) {
  return render(
    <PageShellA
      metrics={METRICS}
      chart={<div data-testid="chart-slot-child">chart</div>}
      tabs={TABS}
      {...overrides}
    />,
  )
}

// 工作台壳测试（unify-ui-design-system T3.1）：指标条 + 主图表 + 底部 Tab 表。
describe('PageShellA：指标条 + 主图表 + 底部 Tab 表', () => {
  it('渲染指标条（每块带 key 级 testid 与 tabular 数值）', () => {
    renderA()
    expect(screen.getByTestId(SHELL_TEST_IDS.aRoot)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.metrics)).toBeInTheDocument()
    const metric = screen.getByTestId(SHELL_TEST_IDS.metric('ret'))
    expect(metric).toHaveTextContent('区间收益')
    expect(metric).toHaveTextContent('+2.31%')
    expect(screen.getByTestId(SHELL_TEST_IDS.metric('dd'))).toHaveTextContent('最大回撤')
  })

  it('主图表插槽承载传入内容', () => {
    renderA()
    expect(screen.getByTestId(SHELL_TEST_IDS.chart)).toContainElement(screen.getByTestId('chart-slot-child'))
  })

  it('底部 Tab 切换内容区并带 150ms 淡入类', () => {
    renderA()
    const content = screen.getByTestId(SHELL_TEST_IDS.tabContent)
    expect(content).toHaveAttribute('data-tab', 'holdings')
    expect(screen.getByText('持仓表内容')).toBeInTheDocument()
    for (const cls of TAB_FADE_CLASS.split(' ')) {
      expect(content.className).toContain(cls)
    }
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.tab('trades')))
    expect(screen.getByTestId(SHELL_TEST_IDS.tabContent)).toHaveAttribute('data-tab', 'trades')
    expect(screen.getByText('交易记录内容')).toBeInTheDocument()
    expect(screen.queryByText('持仓表内容')).not.toBeInTheDocument()
  })

  it('加载中：指标方块 + 图表矩形骨架', () => {
    renderA({ loading: true })
    expect(screen.getByTestId(SHELL_TEST_IDS.skeleton)).toBeInTheDocument()
    expect(screen.getAllByTestId(SHELL_TEST_IDS.skeletonMetric)).toHaveLength(METRICS.length)
    expect(screen.getByTestId(SHELL_TEST_IDS.skeletonChart)).toBeInTheDocument()
    // 加载态不渲染业务内容
    expect(screen.queryByTestId(SHELL_TEST_IDS.tabContent)).not.toBeInTheDocument()
  })

  it('空态：图标 + 一句说明 + 主操作按钮（回调可触发）', () => {
    const onEmptyAction = vi.fn()
    renderA({ empty: true, emptyMessage: '暂无指标数据', emptyActionLabel: '去创建', onEmptyAction })
    expect(screen.getByTestId(SHELL_TEST_IDS.empty)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyIcon)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyMessage)).toHaveTextContent('暂无指标数据')
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.emptyAction))
    expect(onEmptyAction).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId(SHELL_TEST_IDS.metrics)).not.toBeInTheDocument()
  })
})
