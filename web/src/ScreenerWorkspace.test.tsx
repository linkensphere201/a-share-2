// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScreenerWorkspace } from './ScreenerWorkspace'
import { themes } from './themeStore'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({ symbol, asOfDate, highlightedAnalysisItemId }: {
    symbol: string; asOfDate?: string; highlightedAnalysisItemId?: string
  }) => <div data-testid="screener-chart" data-date={asOfDate} data-line={highlightedAnalysisItemId}>{symbol}</div>,
}))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('ScreenerWorkspace', () => {
  it('opens a persisted run and aligns its candidate with the exact analysis line', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate] })
      if (url.includes('/api/analysis/runs/analysis-1')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [run] })
      throw new Error(`unexpected URL ${url}`)
    }))

    render(<ScreenerWorkspace theme={themes[0]} onClose={() => undefined}/>)

    expect((await screen.findAllByText('测试标的')).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByTestId('screener-chart').dataset.line).toBe('major-line-1'))
    expect(screen.getByTestId('screener-chart').dataset.date).toBe('2026-09-01')
    expect(screen.getByText('MDL-1Y-01 · 1年 · 突破回踩')).toBeTruthy()
    expect(screen.getByText('0901 选股结果')).toBeTruthy()
    expect(screen.getByRole('button', { name: '打开趋势分析结果说明' })).toBeTruthy()
  })

  it('filters persisted candidates by breakout state without starting another run', async () => {
    const brokenOut = {
      ...candidate,
      rank: 2,
      symbol: '000002.SZ',
      name: '已突破标的',
      state: 'broken-out',
      analysis_run_id: 'analysis-2',
      line_item_id: 'major-line-2',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate, brokenOut] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [{ ...run, candidate_count: 2 }] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<ScreenerWorkspace theme={themes[0]} onClose={() => undefined}/>)

    expect(await screen.findByText('000001.SZ')).toBeTruthy()
    expect(screen.getAllByText('000002.SZ').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: '快速过滤：已突破' }))

    expect(screen.queryByText('000001.SZ')).toBeNull()
    expect(screen.getAllByText('000002.SZ').length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByTestId('screener-chart').textContent).toBe('000002.SZ'))
  })

  it('starts a new run with the selected periods and states', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/screener/runs?')) return response({ items: [] })
      if (url === '/api/screener/runs' && init?.method === 'POST') return response({ ...run, status: 'running' }, 202)
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<ScreenerWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await screen.findByText('0/10')
    await user.click(screen.getByRole('button', { name: '开始选股' }))

    const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      periods: ['3m', '6m', '1y'],
      states: ['critical-breakout', 'breakout-retest', 'broken-out'],
      max_results: 200,
    })
  })
})

const run = {
  run_id: 'run-1', strategy_id: 'major-descending-breakout', strategy_version: 'v1',
  as_of_date: '2026-09-01', parameters: { periods: ['1y'], states: ['breakout-retest'], max_results: 50 },
  status: 'succeeded', universe_count: 5000, scanned_count: 5000, candidate_count: 1,
  started_at_ms: 1, completed_at_ms: 2,
}

const candidate = {
  rank: 1, symbol: '000001.SZ', name: '测试标的', exchange: 'SZ', kind: 'stock',
  state: 'breakout-retest', score: 88, line_item_id: 'major-line-1', line_code: 'MDL-1Y-01',
  analysis_run_id: 'analysis-1', evidence: {
    period: '1y', as_of_date: '2026-09-01', latest_close: 10.1, projected_price: 10,
    distance_percent: 1, invalidation_price: 9.5, first_target_price: 12, major_target_price: 15,
    first_risk_reward: 3.1, major_risk_reward: 6, first_date: '2025-09-01', second_date: '2026-03-01',
    small_14: { return_percent: 3, recent_half_percent: 2 },
    medium_28: { return_percent: 4, recent_half_percent: 3 },
  },
}

const analysis = {
  run_id: 'analysis-1', as_of_date: '2026-09-01', completion_state: 'complete', stale: false,
  stale_reasons: [], warnings: [], items: [],
}

function response(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}
