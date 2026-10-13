import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PageShellC } from '../../components/shells/PageShellC'
import { SHELL_TEST_IDS } from '../../components/shells/ShellParts'

type Item = { id: string; title: string; detail: string }

const ITEMS: Item[] = [
  { id: 'm1', title: '记忆条款一', detail: '半导体设备景气度上行' },
  { id: 'm2', title: '记忆条款二', detail: '白酒动销走弱' },
]

function renderC(overrides: Partial<Parameters<typeof PageShellC<Item>>[0]> = {}) {
  return render(
    <PageShellC
      items={ITEMS}
      renderItem={item => <span>{item.title}</span>}
      renderDetail={item => <p>{item.detail}</p>}
      {...overrides}
    />,
  )
}

// 审批队列壳测试（unify-ui-design-system T3.3）：左列表 + 右详情 + 确认/驳回。
describe('PageShellC：左列表 + 右详情 + 确认/驳回', () => {
  it('渲染左列表与右侧提示（未选择时无详情）', () => {
    renderC()
    expect(screen.getByTestId(SHELL_TEST_IDS.cRoot)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.list)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.listItem('m1'))).toHaveTextContent('记忆条款一')
    expect(screen.getByTestId(SHELL_TEST_IDS.detail)).not.toHaveTextContent('半导体设备景气度上行')
  })

  it('未选择时确认/驳回禁用；选中后启用并展示详情', () => {
    const onSelect = vi.fn()
    renderC({ onSelect })
    expect(screen.getByTestId(SHELL_TEST_IDS.confirm)).toBeDisabled()
    expect(screen.getByTestId(SHELL_TEST_IDS.reject)).toBeDisabled()
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.listItem('m1')))
    expect(onSelect).toHaveBeenCalledWith('m1')
    expect(screen.getByTestId(SHELL_TEST_IDS.confirm)).toBeEnabled()
    expect(screen.getByTestId(SHELL_TEST_IDS.reject)).toBeEnabled()
    expect(screen.getByTestId(SHELL_TEST_IDS.detail)).toHaveTextContent('半导体设备景气度上行')
  })

  it('确认/驳回回调携带所选条目对象', () => {
    const onConfirm = vi.fn()
    const onReject = vi.fn()
    renderC({ onConfirm, onReject })
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.listItem('m2')))
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.confirm))
    expect(onConfirm).toHaveBeenCalledWith(ITEMS[1])
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.listItem('m1')))
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.reject))
    expect(onReject).toHaveBeenCalledWith(ITEMS[0])
  })

  it('支持受控选中（selectedId 由外部驱动）与初始选中', () => {
    const { unmount } = renderC({ selectedId: null })
    fireEvent.click(screen.getByTestId(SHELL_TEST_IDS.listItem('m1')))
    // 受控模式：外部不更新 selectedId，选择态保持不变
    expect(screen.getByTestId(SHELL_TEST_IDS.confirm)).toBeDisabled()
    unmount()
    renderC({ initialSelectedId: 'm2' })
    expect(screen.getByTestId(SHELL_TEST_IDS.confirm)).toBeEnabled()
    expect(screen.getByTestId(SHELL_TEST_IDS.detail)).toHaveTextContent('白酒动销走弱')
  })

  it('空态：统一空态展示', () => {
    renderC({ items: [], emptyMessage: '暂无待审批条款' })
    expect(screen.getByTestId(SHELL_TEST_IDS.empty)).toBeInTheDocument()
    expect(screen.getByTestId(SHELL_TEST_IDS.emptyMessage)).toHaveTextContent('暂无待审批条款')
  })

  it('加载中：骨架屏（5 行）', () => {
    renderC({ items: [], loading: true })
    expect(screen.getByTestId(SHELL_TEST_IDS.skeleton)).toBeInTheDocument()
    expect(screen.getAllByTestId(SHELL_TEST_IDS.skeletonRow)).toHaveLength(5)
    expect(screen.queryByTestId(SHELL_TEST_IDS.empty)).not.toBeInTheDocument()
  })
})
