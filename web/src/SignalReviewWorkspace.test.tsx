// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SignalReviewWorkspace } from './SignalReviewWorkspace'
import { buildSignalReferenceMap } from './SignalChatPanel'
import type { SignalItem } from './signalReviewClient'
import { themes } from './themeStore'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({ symbol, highlightedAnalysisItemId, trendAnalysisOverride }: {
    symbol: string
    highlightedAnalysisItemId?: string
    trendAnalysisOverride?: { run_id?: string } | null
  }) => <div data-testid="signal-chart" data-highlight={highlightedAnalysisItemId} data-analysis-run={trendAnalysisOverride?.run_id}>{symbol}</div>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SignalReviewWorkspace', () => {
  it('does not link ambiguous evidence aliases from legacy runs', () => {
    const duplicate = {
      ...items[1], evidence: [{
        evidence_id: 'e2', alias: 'S1', evidence_type: 'board-recognition-ranking',
        payload: { board_name: 'Legacy' },
      }],
    }
    expect(buildSignalReferenceMap(
      [items[0], duplicate] as unknown as SignalItem[],
    ).has('S1')).toBe(false)
  })

  it('loads an immutable run, filters changes, and opens its chart evidence', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [run] })
      if (url.endsWith('/items')) return response({ items })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    const result = await screen.findByText('000001.SZ')
    expect(screen.queryByTestId('signal-chart')).toBeNull()
    await user.click(result.closest('button')!)
    expect(screen.getByTestId('signal-chart').textContent).toBe('000001.SZ')
    expect(screen.getByText('[S1]')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '移除' }))
    expect(screen.getByText('旧标的')).toBeTruthy()
    expect(screen.queryByText('测试标的')).toBeNull()
    expect(screen.queryByTestId('signal-chart')).toBeNull()
  })

  it('starts a run only from the explicit manual action', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [] })
      if (url.includes('/api/signals/weekly-board-recognition/runs') && init?.method === 'POST') {
        return response({ ...run, status: 'running', phase: 'queued' }, 202)
      }
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await screen.findByText('尚未运行')
    expect(fetchMock.mock.calls.some(call => call[1]?.method === 'POST')).toBe(false)
    await user.click(screen.getByRole('button', { name: '运行本期信号' }))
    expect(fetchMock.mock.calls.some(call => call[1]?.method === 'POST')).toBe(true)
  })

  it('binds Codex chat to the selected signal result and its run', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [run] })
      if (url.endsWith('/items')) return response({ items })
      if (url === '/api/ai/codex/status') return response({
        codex: { available: true, authenticated: true, experimental: true },
        templates: [], signal_templates: [{ id: 'signal-challenge', version: 'v1', label: '反例质疑', instruction: 'test' }],
      })
      if (url === '/api/ai/conversations' && init?.method === 'POST') return response(conversation, 201)
      if (url.startsWith('/api/ai/conversations?')) return response({ items: [{
        ...conversation, turn_count: 0, created_at_ms: 1, updated_at_ms: 1,
      }] })
      if (url.endsWith('/turns') && init?.method === 'POST') return response({ turn_id: 'turn-1', status: 'queued' }, 202)
      throw new Error(`unexpected URL ${url}`)
    })
    class FakeEventSource {
      onerror: (() => void) | null = null
      constructor(public url: string) {}
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('EventSource', FakeEventSource)
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click((await screen.findByText('000001.SZ')).closest('button')!)
    await user.click(screen.getByRole('button', { name: 'Codex 信号讨论' }))
    const input = await screen.findByRole('textbox', { name: '信号讨论输入' })
    await user.type(input, '复核这个变化')
    await user.click(screen.getByRole('button', { name: '发送信号问题' }))

    const create = fetchMock.mock.calls.find(call => String(call[0]) === '/api/ai/conversations')
    expect(JSON.parse(String(create?.[1]?.body))).toMatchObject({
      context_kind: 'signal_run', context_id: 'run-1',
    })
    const turn = fetchMock.mock.calls.find(call => String(call[0]).endsWith('/turns'))
    expect(JSON.parse(String(turn?.[1]?.body))).toMatchObject({
      content: '复核这个变化', selected_signal_item_ids: ['item-1'],
    })
  })

  it('searches complete daily observations and pins one into the attention registry', async () => {
    let pinned = false
    const dailyDefinition = {
      ...definition, signal_id: 'daily-market-board-review', name: '每日大盘与板块复盘',
      cadence: 'daily', profiles: ['market', 'attention'],
    }
    const dailyRun = { ...run, signal_id: dailyDefinition.signal_id, cadence: 'daily' }
    const observation = {
      run_id: 'run-1', symbol: 'BK001.DC', name: '测试板块', exchange: 'DC',
      effective_date: '2026-09-04', coverage_state: 'complete',
      state_codes: ['bullish-transition-candidate'],
      metrics: { returns: { 5: .03, 20: .08 }, volume_ratio20: 1.2 },
      disqualifiers: [], attention_reasons: ['bullish-boundary-proximity'],
      attention_eligible: true, deep_analysis_state: 'pending', input_digest: 'digest',
      conclusion_code: 'bullish-transition-candidate',
      rendered_summary: '【临界状态】多头临界\n- 近期对比：首次观察。',
      comparison: { transition: 'new' },
      algorithm_version: 'daily-v1', config_version: 'config-v1',
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [dailyDefinition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [dailyRun] })
      if (url.endsWith('/items')) return response({ items: [] })
      if (url.includes('/board-observations?')) return response({ items: [observation], total: 1 })
      if (url.endsWith('/attention') && init?.method !== 'PUT') return response({ items: pinned ? [{
        signal_id: dailyDefinition.signal_id, symbol: observation.symbol,
        name: observation.name, exchange: 'DC', status: 'manual-pinned',
        manual_pinned: true, first_observed_date: '2026-09-04',
        last_observed_date: '2026-09-04', reasons: ['manual-user-selection'],
      }] : [] })
      if (url.includes('/attention/BK001.DC') && init?.method === 'PUT') {
        pinned = true
        return response({ symbol: observation.symbol, manual_pinned: true, status: 'manual-pinned' })
      }
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click(await screen.findByRole('button', { name: /全部观察/ }))
    await user.click((await screen.findByText('BK001.DC')).closest('button')!)
    expect(screen.getByTestId('signal-chart').textContent).toBe('BK001.DC')
    expect(screen.getAllByText(/多头临界/).length).toBeGreaterThan(0)
    expect(screen.getByText(/接近下降边界/)).toBeTruthy()
    expect(screen.getByText(/确认：放量收于边界上方/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '加入手工观察池' }))

    expect(fetchMock.mock.calls.some(call =>
      String(call[0]).includes('/attention/BK001.DC') && call[1]?.method === 'PUT'
    )).toBe(true)
    expect(await screen.findByRole('button', { name: '取消手工固定' })).toBeTruthy()
  })

  it('loads the exact M4 run and highlights the cited analysis item', async () => {
    const dailyDefinition = {
      ...definition, signal_id: 'daily-market-board-review', cadence: 'daily',
      profiles: ['market', 'attention'],
    }
    const dailyRun = { ...run, signal_id: dailyDefinition.signal_id, cadence: 'daily' }
    const dailyItem = {
      ...items[0], symbol: 'BK001.DC', name: '测试板块', profile: 'attention',
      payload: { rendered_summary: '固定结论', deep_analysis_run_id: 'deep-1' },
      evidence: [{
        evidence_id: 'm4-e1', alias: 'S1', evidence_type: 'm4-line',
        source_run_id: 'deep-1', source_item_id: 'line-1', payload: {},
      }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [dailyDefinition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [dailyRun] })
      if (url.endsWith('/items')) return response({ items: [dailyItem] })
      if (url.endsWith('/attention')) return response({ items: [] })
      if (url === '/api/analysis/runs/deep-1') return response({ run_id: 'deep-1', items: [] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click((await screen.findByText('BK001.DC')).closest('button')!)
    expect(await screen.findByText('固定结论')).toBeTruthy()
    expect(screen.getByTestId('signal-chart').dataset.analysisRun).toBe('deep-1')
    await user.click(screen.getByText('[S1]').closest('button')!)
    expect(screen.getByTestId('signal-chart').dataset.highlight).toBe('line-1')
  })
})

const definition = {
  signal_id: 'weekly-board-recognition', name: '板块辨识度周观察',
  description: '测试说明', cadence: 'weekly', definition_version: 'v1',
  algorithm_version: 'a1', manual_only: true, profiles: ['recent', 'historical'],
}

const run = {
  run_id: 'run-1', signal_id: definition.signal_id, definition_version: 'v1',
  algorithm_version: 'a1', cadence: 'weekly', effective_date: '2026-09-04',
  revision: 2, prior_run_id: 'run-0', status: 'succeeded', phase: 'completed',
  work_total: 100, work_done: 100, item_count: 1, added_count: 1,
  retained_count: 0, removed_count: 1, summary: {}, started_at_ms: 1, completed_at_ms: 2,
}

const items = [{
  item_id: 'item-1', item_key: 'recent:000001.SZ', rank: 1,
  symbol: '000001.SZ', name: '测试标的', kind: 'stock', exchange: 'SZ',
  profile: 'recent', change_type: 'added', active: true, score: .91,
  confidence: .72, payload: { board_count: 2, board_names: ['CPO', '算力'] },
  evidence: [{ evidence_id: 'e1', alias: 'S1', evidence_type: 'board-recognition-ranking', payload: { board_name: 'CPO', board_classification: 'concept', rank: 1, score: .91 } }],
}, {
  item_id: 'item-2', item_key: 'historical:000002.SZ', rank: 1,
  symbol: '000002.SZ', name: '旧标的', kind: 'stock', exchange: 'SZ',
  profile: 'historical', change_type: 'removed', active: false, score: .82,
  confidence: .62, payload: { board_count: 2 }, evidence: [],
}]

const conversation = {
  conversation_id: 'conversation-1', context_kind: 'signal_run', context_id: 'run-1',
  symbol: null, timeframe: null, source_run_id: null, as_of_date: '2026-09-04',
  algorithm_version: 'a1', config_version: 'v1', completion_state: 'complete',
  preview: false, title: 'weekly · 2026-09-04 R2', status: 'active', turns: [],
}

function response(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}
