// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TradeSimulationWorkspace } from './TradeSimulationWorkspace'
import type { SimulationInput } from './tradeSimulationClient'

const example: SimulationInput = { strategy_id: 'trend-trade-v1', label: '合成情景：目标退出', signal_session: '2000-01-03', capital: 100000,
  upper: 10, lower: 9.5, signal_close: 10.2, atr20: .4, target: 12.5, median_amount20: 1e8,
  lot_size: 100, fee_bps: 10, slippage_bps: 5, eligibility_assumed: true,
  sessions: ['2000-01-04', '2000-01-05'].map(session => ({ session, open: 10.25, close: 10.5,
    atr20: .4, lower_limit: 9, upper_limit: 12, tradable: true, verified: true, market_risk_off: false })) }
const catalog = { items: [{ strategy_id: 'trend-trade-v1', name: '趋势突破型', version: 'trend-trade-v1', scenarios: [example] }] }
const result = { strategy_id: 'trend-trade-v1', strategy_name: '趋势突破型', strategy_version: 'trend-trade-v1', input: example, input_digest: 'abc123', status: 'held', planned_quantity: 500,
  plan: { ceiling: 10.404, stop: 9.6, target: 12.5 }, summary: { net_return: .001, max_drawdown: .002,
    realized_pnl: null, open_quantity: 500, fees: 5, stale_valuation: false },
  equity: [{ session: '2000-01-04', equity: 100100, cash: 95000, quantity: 500, stop: 9.6, stale: false }],
  events: [{ session: '2000-01-04', kind: 'entry', reason: 'next-open', price: 10.25, quantity: 500 }] }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('TradeSimulationWorkspace', () => {
  it('starts empty, labels the scope and returns to the workspace', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(catalog))))
    const close = vi.fn()
    render(<TradeSimulationWorkspace onClose={close}/>)
    expect(screen.getByText('手工情景 · 非历史回测')).toBeTruthy()
    expect(screen.getByText('尚无模拟结果')).toBeTruthy()
    expect((screen.getByRole('button', { name: '运行模拟' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '返回主界面' }))
    expect(close).toHaveBeenCalledOnce()
    await screen.findByRole('option', { name: example.label })
  })

  it('runs the server engine once, renders events and switches to equity', async () => {
    let finish: (value: Response) => void = () => undefined
    const fetcher = vi.fn((url: string) => url.endsWith('/strategies')
      ? Promise.resolve(new Response(JSON.stringify(catalog)))
      : new Promise<Response>(resolve => { finish = resolve }))
    vi.stubGlobal('fetch', fetcher)
    render(<TradeSimulationWorkspace onClose={() => undefined}/>)
    await screen.findByRole('option', { name: example.label })
    fireEvent.change(screen.getByLabelText('载入合成测试情景'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '运行模拟' }))
    expect((screen.getByRole('button', { name: '模拟中…' }) as HTMLButtonElement).disabled).toBe(true)
    finish(new Response(JSON.stringify(result)))
    await screen.findByText('买入成交')
    expect(screen.getByRole('img', { name: '模拟账户权益曲线' })).toBeTruthy()
    expect(fetcher).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('tab', { name: '逐日权益' }))
    expect(screen.getByText('有效')).toBeTruthy()
    expect(screen.getByText('下期保护线')).toBeTruthy()
  })

  it('surfaces rejected input without manufacturing a result', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(url.endsWith('/strategies')
      ? new Response(JSON.stringify(catalog))
      : new Response(JSON.stringify({ detail: 'invalid breakout geometry' }), { status: 422 }))))
    render(<TradeSimulationWorkspace onClose={() => undefined}/>)
    await screen.findByRole('option', { name: example.label })
    fireEvent.change(screen.getByLabelText('载入合成测试情景'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '运行模拟' }))
    expect((await screen.findByRole('alert')).textContent).toContain('invalid breakout geometry')
    expect(screen.getByText('尚无模拟结果')).toBeTruthy()
  })

  it('supports editing and bounded session removal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }))))
    render(<TradeSimulationWorkspace onClose={() => undefined}/>)
    fireEvent.click(screen.getByRole('button', { name: '增加会话' }))
    expect(screen.getByLabelText('日期 3')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '移除会话 3' }))
    expect(screen.queryByLabelText('日期 3')).toBeNull()
    await waitFor(() => expect((screen.getByRole('button', { name: '移除会话 1' }) as HTMLButtonElement).disabled).toBe(true))
  })

  it('switches catalog strategies without carrying old geometry and sends its identity', async () => {
    const other = { ...catalog.items[0], strategy_id: 'test-policy', name: '测试策略', version: 'test-v1',
      scenarios: [{ ...example, strategy_id: 'test-policy', label: '第二策略情景' }] }
    const fetcher = vi.fn((url: string, init?: RequestInit) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith('/strategies') ? { items: [...catalog.items, other] }
        : { ...result, input: JSON.parse(String(init?.body)), strategy_id: 'test-policy', strategy_name: '测试策略', strategy_version: 'test-v1' }))))
    vi.stubGlobal('fetch', fetcher)
    render(<TradeSimulationWorkspace onClose={() => undefined}/>)
    await screen.findByRole('option', { name: '测试策略（test-v1）' })
    fireEvent.change(screen.getByLabelText('载入合成测试情景'), { target: { value: '0' } })
    fireEvent.change(screen.getByLabelText('执行策略'), { target: { value: 'test-policy' } })
    expect((screen.getByLabelText('箱顶') as HTMLInputElement).value).toBe('0')
    expect((screen.getByRole('button', { name: '运行模拟' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('载入合成测试情景'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '运行模拟' }))
    await screen.findByText('买入成交')
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).strategy_id).toBe('test-policy')
    expect(screen.getByText('测试策略 · test-v1')).toBeTruthy()
  })
})
