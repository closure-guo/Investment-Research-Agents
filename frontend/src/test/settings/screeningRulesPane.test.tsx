// ScreeningRulesPane 选股规则分区（delta add-screening-rules-config-ui）
// stub fetch 按 URL 分发并记录调用，仿 sessionsPane.test.tsx 范式
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ScreeningRulesPane } from '../../pages/settings/panes/ScreeningRulesPane'

const GET_BODY = {
  min_trading_days: { value: 60, source: 'default', default: 60, min: 1, max: 250, env_value: null },
  min_avg_amount_20d: {
    value: 50000000, source: 'default', default: 50000000, min: 1000000, max: 1000000000, env_value: null,
  },
  l2_top_n: { value: 100, source: 'default', default: 100, min: 1, max: 500, env_value: null },
  file_status: 'missing',
  updated_at: null,
}

function stubFetch(calls: Array<{ url: string; init?: RequestInit }>, opts?: { getFail?: boolean }) {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init })
    if (url.endsWith('/api/settings/screening-rules')) {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Record<string, number>
        if (body.l2_top_n === 0) {
          return Promise.resolve(new Response(
            JSON.stringify({ detail: 'l2_top_n 必须是 1–500 范围内的合法数值: 0' }), { status: 400 },
          ))
        }
        return Promise.resolve(new Response(JSON.stringify({
          ...GET_BODY,
          min_trading_days: { ...GET_BODY.min_trading_days, value: body.min_trading_days, source: 'file' },
          min_avg_amount_20d: { ...GET_BODY.min_avg_amount_20d, value: body.min_avg_amount_20d, source: 'file' },
          l2_top_n: { ...GET_BODY.l2_top_n, value: body.l2_top_n, source: 'file' },
          file_status: 'ok',
          updated_at: '2026-10-28T00:00:00+08:00',
        })))
      }
      if (opts?.getFail) {
        return Promise.resolve(new Response(JSON.stringify({ error: 'boom' }), { status: 500 }))
      }
      return Promise.resolve(new Response(JSON.stringify(GET_BODY)))
    }
    return Promise.resolve(new Response('{}'))
  }))
}

describe('ScreeningRulesPane 选股规则分区', () => {
  let calls: Array<{ url: string; init?: RequestInit }>
  beforeEach(() => {
    calls = []
    stubFetch(calls)
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  function putCalls() {
    return calls.filter(c => c.url.endsWith('/api/settings/screening-rules') && c.init?.method === 'PUT')
  }

  it('默认渲染三项规则：成交额按万元显示、来源「默认值」、生效时机说明', async () => {
    render(<ScreeningRulesPane />)
    expect(await screen.findByTestId('screening-rules-pane')).toBeInTheDocument()
    expect(screen.getByTestId('rules-input-min-trading-days')).toHaveValue(60)
    expect(screen.getByTestId('rules-input-min-avg-amount-wan')).toHaveValue(5000)
    expect(screen.getByTestId('rules-input-l2-top-n')).toHaveValue(100)
    expect(screen.getAllByText('默认值').length).toBe(3)
    expect(screen.getByText(/下一次选股日批或手动补跑开始时生效/)).toBeInTheDocument()
  })

  it('万元输入保存：PUT 体换算为元、成功显示「已保存」且来源更新', async () => {
    render(<ScreeningRulesPane />)
    await screen.findByTestId('screening-rules-pane')
    fireEvent.change(screen.getByTestId('rules-input-min-avg-amount-wan'), { target: { value: '8000' } })
    fireEvent.click(screen.getByTestId('rules-save'))
    await waitFor(() => expect(screen.getByTestId('rules-saved')).toBeInTheDocument())
    expect(putCalls()).toHaveLength(1)
    const body = JSON.parse(String(putCalls()[0].init?.body)) as Record<string, number>
    expect(body).toEqual({ min_trading_days: 60, min_avg_amount_20d: 80000000, l2_top_n: 100 })
    expect(screen.getAllByText('配置文件').length).toBe(3)
  })

  it('非法输入前端拦截：不发 PUT、展示区间错误', async () => {
    render(<ScreeningRulesPane />)
    await screen.findByTestId('screening-rules-pane')
    fireEvent.change(screen.getByTestId('rules-input-l2-top-n'), { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('rules-save'))
    await waitFor(() => expect(screen.getByTestId('rules-save-error')).toBeInTheDocument())
    expect(putCalls()).toHaveLength(0)
  })

  it('后端 400：原位展示后端 detail', async () => {
    calls = []
    vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init })
      if (url.endsWith('/api/settings/screening-rules') && init?.method === 'PUT') {
        return Promise.resolve(new Response(JSON.stringify({ detail: '保存被后端拒绝' }), { status: 400 }))
      }
      return Promise.resolve(new Response(JSON.stringify(GET_BODY)))
    }))
    render(<ScreeningRulesPane />)
    await screen.findByTestId('screening-rules-pane')
    fireEvent.change(screen.getByTestId('rules-input-min-trading-days'), { target: { value: '80' } })
    fireEvent.click(screen.getByTestId('rules-save'))
    await waitFor(() => expect(screen.getByTestId('rules-save-error')).toHaveTextContent('保存被后端拒绝'))
  })

  it('加载失败：原位错误提示 + 重试，不影响渲染骨架', async () => {
    stubFetch(calls, { getFail: true })
    render(<ScreeningRulesPane />)
    expect(await screen.findByTestId('rules-load-error')).toBeInTheDocument()
    expect(screen.queryByTestId('rules-save')).not.toBeInTheDocument()
  })
})
