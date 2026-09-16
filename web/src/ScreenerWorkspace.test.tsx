// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScreenerWorkspace } from './ScreenerWorkspace'
import { themes } from './themeStore'
import { fetchInstrumentBoardMemberships } from './boardTags'

vi.mock('./boardTags', () => ({ fetchInstrumentBoardMemberships: vi.fn() }))
beforeEach(() => { vi.mocked(fetchInstrumentBoardMemberships).mockReset().mockResolvedValue({}) })

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({ symbol, asOfDate, highlightedAnalysisItemId, toolbarContent }: {
    symbol: string; asOfDate?: string; highlightedAnalysisItemId?: string; toolbarContent?: ReactNode
  }) => <div data-testid="screener-chart" data-date={asOfDate} data-line={highlightedAnalysisItemId}>{symbol}{toolbarContent}</div>,
}))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('ScreenerWorkspace', () => {
  it.each(['strong-first-pullback', 'low-base-platform-pullback'])('runs %s and filters observation versus confirmation', async (strategyId) => {
    const firstRun = { ...run, strategy_id: strategyId, strategy_version: `${strategyId}-v1` }
    const confirmed = { ...candidate, state: 'pullback-confirmed', evidence: {
      ...candidate.evidence, kind: 'first-pullback-range', stage: 'pullback-confirmed',
      launch_date: '2026-08-10', confirmation_date: '2026-09-01', pullback_sessions: 5,
    } }
    const observation = { ...confirmed, symbol: '000002.SZ', name: '观察标的', rank: 2,
      state: 'pullback-observation', evidence: { ...confirmed.evidence, stage: 'pullback-observation', confirmation_date: null } }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return response(firstRun, 202)
      if (String(input).includes('/candidates')) return response({ items: [confirmed, observation] })
      if (String(input).includes('/api/analysis/')) return response(analysis)
      return response({ items: [firstRun] })
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderScreener()
    await screen.findByText('观察标的', { selector: 'b' })
    expect(screen.getByText('仅日线量价；板块共振、分时承接未验证')).toBeTruthy()
    expect(screen.queryByText('距斜边')).toBeNull()
    await user.click(screen.getByRole('button', { name: '快速过滤：转强确认' }))
    expect(screen.queryByText('观察标的', { selector: 'b' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '快速过滤：回踩观察' }))
    expect(screen.queryByText('测试标的', { selector: 'b' })).toBeNull()
    expect(await screen.findByText('尚未确认')).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('策略'), strategyId)
    await user.click(screen.getByRole('button', { name: '开始选股' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]?.body)).strategy_id).toBe(strategyId)
  })

  it('defaults V7 to compact platforms and shows dated recognition beside names', async () => {
    const compact = { ...candidate, state: 'accumulating', evidence: {
      ...candidate.evidence, platform_style: 'compact-platform', recognition_rank_bonus: 5,
      compact_platform: { qualified: true, score: 83, reasons: [], last10: { range_percent: 3, small_body_fraction: .9 } },
    }, recognition: { available: true, source_date: '2026-09-04', source_run_id: 'weekly', tags: ['recent', 'historical'] } }
    const broad = { ...compact, symbol: '000002.SZ', name: '宽幅标的', rank: 2,
      evidence: { ...compact.evidence, platform_style: 'broad-base' } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [compact, broad] })
      if (url.includes('/api/analysis/')) return response(analysis)
      return response({ items: [{ ...run, strategy_id: 'volume-accumulation-20d', strategy_version: 'volume-accumulation-20d-v7' }] })
    }))
    const user = userEvent.setup()
    renderScreener()
    await screen.findByText('测试标的', { selector: 'b' })
    expect(screen.queryByText('宽幅标的')).toBeNull()
    expect(screen.getByText('近期辨识度').getAttribute('title')).toContain('2026-09-04')
    expect(screen.getByText('近期辨识度').getAttribute('title')).toContain('排序加分 5')
    await user.click(screen.getByRole('button', { name: /^全部形态/ }))
    expect(screen.getByText('宽幅标的')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /^紧凑平台/ }))
    expect(screen.queryByText('宽幅标的')).toBeNull()
  })

  it('keeps polling the active run while an older result is selected', async () => {
    const active = { ...run, run_id: 'active', as_of_date: '2026-09-02', status: 'running' }
    let completed = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/active')) { completed = true; return response({ ...active, status: 'succeeded' }) }
      if (url.includes('/candidates')) return response({ items: [] })
      return response({ items: [{ ...active, status: completed ? 'succeeded' : 'running' }, run] })
    }))
    renderScreener()
    fireEvent.click(await screen.findByRole('button', { name: /0901 选股结果/ }))
    const start = screen.getByRole('button', { name: '开始选股' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    await waitFor(() => expect(start.disabled).toBe(false), { timeout: 2500 })
    expect(screen.getByRole('button', { name: /0901 选股结果/ }).className).toContain('active')
  })

  it('shows startup immediately, prevents duplicate posts, and replaces it with server progress', async () => {
    let finish!: (value: Response) => void
    const pending = new Promise<Response>(resolve => { finish = resolve })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return pending
      if (String(input).includes('/candidates')) return response({ items: [] })
      return response({ items: [] })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderScreener()
    await screen.findByText('暂无历史结果')
    const start = screen.getByRole('button', { name: '开始选股' }) as HTMLButtonElement
    fireEvent.click(start)
    expect(screen.getByRole('status').textContent).toContain('正在创建选股任务')
    expect(start.disabled).toBe(true)
    fireEvent.click(start)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    await act(async () => { finish(await response({ ...run, status: 'running', scanned_count: 0, universe_count: 5000 }, 202)) })
    await screen.findByText('大斜边突破 · 0/5000')
    expect(screen.queryByRole('status')).toBeNull()
    expect(start.disabled).toBe(true)
  })

  it('clears startup on failure and allows retry', async () => {
    let fail!: (reason: Error) => void
    const pending = new Promise<Response>((_, reject) => { fail = reject })
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return pending
      return response({ items: [] })
    }))
    renderScreener()
    await screen.findByText('暂无历史结果')
    const start = screen.getByRole('button', { name: '开始选股' }) as HTMLButtonElement
    fireEvent.click(start)
    fail(new Error('启动失败，请重试'))
    await screen.findByText('启动失败，请重试')
    expect(screen.queryByRole('status')).toBeNull()
    expect(start.disabled).toBe(false)
  })

  it('does not let a late initial history response erase the newly created run', async () => {
    let finishHistory!: (value: Response) => void
    const history = new Promise<Response>(resolve => { finishHistory = resolve })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return response({ ...run, status: 'running' }, 202)
      if (String(input).includes('/candidates')) return response({ items: [] })
      return history
    }))
    renderScreener()
    fireEvent.click(screen.getByRole('button', { name: '开始选股' }))
    await screen.findByText('0901 选股结果')
    await act(async () => { finishHistory(await response({ items: [] })) })
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(screen.getByText('0901 选股结果')).toBeTruthy()
  })

  it('unions selected boards and intersects state and historical filters without rescanning', async () => {
    const recognition = { available: true, source_date: '2026-08-31', source_run_id: 'weekly-1', tags: ['historical'] }
    const pcb = { board_symbol: 'PCB.DC', name: 'PCB', classification: 'concept' as const, source_system: 'eastmoney' }
    const components = { ...pcb, board_symbol: 'COMP.TI', name: '元件', classification: 'industry' as const }
    vi.mocked(fetchInstrumentBoardMemberships).mockResolvedValue({
      '000001.SZ': [pcb], '000002.SZ': [components], '000003.SZ': [pcb, components],
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [
        { ...candidate, recognition },
        { ...candidate, rank: 2, symbol: '000002.SZ', name: '元件标的', state: 'broken-out', recognition },
        { ...candidate, rank: 3, symbol: '000003.SZ', name: '双板块标的', recognition: { ...recognition, tags: [] } },
      ] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [{ ...run, candidate_count: 3 }] })
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderScreener()
    await user.click(await screen.findByRole('button', { name: 'PCB 2' }))
    expect(screen.queryByText('000002.SZ')).toBeNull()
    await user.type(screen.getByRole('searchbox', { name: '搜索板块' }), '元件')
    expect(screen.getByRole('button', { name: 'PCB 2' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '元件 2' }))
    expect(screen.getByText(/显示 3\/3/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /^历史辨识度/ }))
    expect(screen.getByText(/显示 2\/3/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '快速过滤：已突破' }))
    expect(screen.getByText(/显示 1\/3/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '元件 1' }))
    expect(screen.getByText('当前筛选组合没有符合条件的标的')).toBeTruthy()
    expect(screen.queryByTestId('screener-chart')).toBeNull()
    await user.click(screen.getByRole('button', { name: '清除板块筛选' }))
    expect(screen.getByText(/显示 1\/3/)).toBeTruthy()
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/candidates'))).toHaveLength(1)
    expect(fetchInstrumentBoardMemberships).toHaveBeenCalledTimes(1)
  })

  it('keeps unfiltered results available when board loading fails and retries explicitly', async () => {
    vi.mocked(fetchInstrumentBoardMemberships).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({})
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      return response({ items: [run] })
    }))
    renderScreener()
    const retry = await screen.findByRole('button', { name: /板块加载失败/ })
    expect(screen.getAllByText('000001.SZ').length).toBeGreaterThan(0)
    await userEvent.click(retry)
    expect(await screen.findByText('暂无板块数据')).toBeTruthy()
  })

  it('intersects historical recognition with state filters without rerunning screening', async () => {
    const recognition = { available: true, source_date: '2026-08-31', source_run_id: 'weekly-1', tags: ['historical'] }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [
        { ...candidate, recognition },
        { ...candidate, rank: 2, symbol: '000002.SZ', name: '普通标的', state: 'broken-out', recognition: { ...recognition, tags: [] } },
      ] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [{ ...run, candidate_count: 2 }] })
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderScreener()
    await screen.findByText('000002.SZ')
    await user.click(screen.getByRole('button', { name: /^历史辨识度/ }))
    expect(screen.queryByText('000002.SZ')).toBeNull()
    expect(screen.getByText(/显示 1\/2/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '快速过滤：已突破' }))
    expect(screen.queryByText('000001.SZ')).toBeNull()
    expect(screen.getByText(/显示 0\/2/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /不限标签/ }))
    expect(screen.getAllByText('000002.SZ').length).toBeGreaterThan(0)
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/candidates'))).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/screener/runs')).toBe(false)
  })

  it('opens a persisted run and aligns its candidate with the exact analysis line', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate] })
      if (url.includes('/api/analysis/runs/analysis-1')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [run] })
      throw new Error(`unexpected URL ${url}`)
    }))

    renderScreener()

    expect((await screen.findAllByText('测试标的')).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByTestId('screener-chart').dataset.line).toBe('major-line-1'))
    expect(screen.getByTestId('screener-chart').dataset.date).toBe('2026-09-01')
    expect(screen.getByText('MDL-1Y-01 · 1年 · 突破回踩')).toBeTruthy()
    expect(screen.getByText('0901 选股结果')).toBeTruthy()
    expect(screen.getByRole('button', { name: '打开形态分析结果' })).toBeTruthy()
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
    renderScreener()

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
    renderScreener()

    await screen.findByText('0/10')
    await user.click(screen.getByRole('button', { name: '开始选股' }))

    const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      strategy_id: 'major-descending-breakout',
      periods: ['6m', '1y'],
      states: ['critical-breakout', 'breakout-retest', 'broken-out'],
      max_results: 200,
    })
  })

  it('runs volume accumulation as an independent strategy', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/screener/runs?')) return response({ items: [] })
      if (url === '/api/screener/runs' && init?.method === 'POST') return response({
        ...run,
        strategy_id: 'volume-accumulation-20d',
        status: 'running',
      }, 202)
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderScreener()

    await screen.findByText('0/10')
    await user.selectOptions(screen.getByLabelText('策略'), 'volume-accumulation-20d')
    expect(screen.queryByText('周期')).toBeNull()
    expect(screen.queryByText('状态')).toBeNull()
    await user.click(screen.getByRole('button', { name: '开始选股' }))

    const request = fetchMock.mock.calls.find(call => call[1]?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      strategy_id: 'volume-accumulation-20d',
      max_results: 200,
    })
  })

  it('does not expose the retired exclusion pool for accumulation screening', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/screener/runs?')) return response({ items: [] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    renderScreener()

    await screen.findByText('0/10')
    await user.selectOptions(screen.getByLabelText('策略'), 'volume-accumulation-20d')
    expect(screen.queryByRole('button', { name: /剔除池/ })).toBeNull()
    expect(screen.queryByRole('dialog', { name: /剔除池/ })).toBeNull()
  })

  it('deletes a finished run from its context menu after confirmation', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [run] })
      if (url === '/api/screener/runs/run-1' && init?.method === 'DELETE') {
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderScreener()

    const runButton = (await screen.findByText('0901 选股结果')).closest('button')!
    fireEvent.contextMenu(runButton, { clientX: 40, clientY: 50 })
    await user.click(screen.getByRole('menuitem', { name: '删除本轮结果' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/screener/runs/run-1', { method: 'DELETE' },
    ))
    expect(screen.queryByText('0901 选股结果')).toBeNull()
    expect(screen.getByText('已删除 0901 选股结果')).toBeTruthy()
  })

  it('adds a candidate to a selected writable list in the current group', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/candidates')) return response({ items: [candidate] })
      if (url.includes('/api/analysis/runs/')) return response(analysis)
      if (url.includes('/api/screener/runs?')) return response({ items: [run] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const add = vi.fn(() => true)
    const user = userEvent.setup()
    renderScreener({
      targetLists: [{ id: 'list-1', title: '候选观察', instrumentCount: 3 }],
      onAddCandidateToList: add,
    })

    const resultButton = (await screen.findAllByText('000001.SZ'))[0].closest('button')!
    fireEvent.contextMenu(resultButton, { clientX: 250, clientY: 120 })
    expect(screen.getByRole('menuitem', { name: '添加到…' })).toBeTruthy()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menuitem', { name: '添加到…' })).toBeNull()
    fireEvent.contextMenu(resultButton, { clientX: 250, clientY: 120 })
    await user.click(screen.getByRole('menuitem', { name: '添加到…' }))
    await user.click(screen.getByRole('menuitem', { name: '候选观察3 个标的' }))

    expect(add).toHaveBeenCalledWith('list-1', candidate)
    expect(screen.getByText('已将 测试标的 添加到 候选观察')).toBeTruthy()
  })
})

function renderScreener(overrides: Partial<Parameters<typeof ScreenerWorkspace>[0]> = {}) {
  return render(<ScreenerWorkspace
    theme={themes[0]}
    onClose={() => undefined}
    targetLists={[]}
    onAddCandidateToList={() => false}
    {...overrides}
  />)
}

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
