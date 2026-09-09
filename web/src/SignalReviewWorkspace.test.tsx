// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SignalReviewWorkspace, stateDetailLabels } from './SignalReviewWorkspace'
import { buildSignalReferenceMap } from './SignalChatPanel'
import type { ObservationPoolItem, SignalItem } from './signalReviewClient'
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
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('SignalReviewWorkspace', () => {
  it('keeps simultaneous volume and descending-envelope anomalies visible', () => {
    expect(stateDetailLabels([
      'bullish-boundary-triggered', 'descending-envelope-3m-broken',
      'descending-envelope-6m-broken', 'sudden-volume-expansion',
    ])).toEqual(['3月斜边突破', '6月斜边突破', '突然放量'])
  })

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

  it('maps selected observation-pool sources to O-series references', () => {
    const poolItem = {
      symbol: '300001.SZ', name: '池内标的', kind: 'stock', exchange: 'SZ',
      lifecycle_state: 'active', rank: 1, payload: {},
      sources: [{
        source_type: 'm4-analysis', source_reference: 'm4-1',
        source_entity_key: 'line-1', reason: '结构证据', payload: {},
      }],
    }
    expect(buildSignalReferenceMap(
      [], poolItem as ObservationPoolItem,
    ).get('O1')).toBe('pool:stock:300001.SZ\u0000m4-analysis:line-1')
  })

  it('loads an immutable run, filters changes, and opens its chart evidence', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [run] })
      if (url.endsWith('/items')) return response({ items })
      if (url.endsWith('/scores')) return response({ items: [] })
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    const result = await screen.findByText('000001.SZ')
    expect(screen.queryByTestId('signal-chart')).toBeNull()
    const resultButton = result.closest('button')!
    await user.click(resultButton)
    expect(resultButton.classList.contains('active')).toBe(true)
    expect(screen.getByTestId('signal-chart').textContent).toBe('000001.SZ')
    expect(screen.getByText('[S1]')).toBeTruthy()
    const resultWidthSeparator = screen.getByRole('separator', { name: '调整复盘结果栏宽度' })
    fireEvent.pointerDown(resultWidthSeparator, { clientX: 500 })
    fireEvent.pointerMove(window, { clientX: 560 })
    fireEvent.pointerUp(window)
    await vi.waitFor(() => expect(JSON.parse(window.localStorage.getItem(
      'stock-harness.signal-review.column-widths.v1',
    ) ?? '{}').results).toBe(420))
    const separator = screen.getByRole('separator', { name: '调整固定算法结论高度' })
    const inspector = separator.closest('.signal-inspector') as HTMLElement
    Object.defineProperty(inspector, 'clientHeight', { configurable: true, value: 800 })
    fireEvent.pointerDown(separator, { clientY: 500 })
    fireEvent.pointerMove(window, { clientY: 400 })
    fireEvent.pointerUp(window)
    await vi.waitFor(() => expect(window.localStorage.getItem(
      'stock-harness.signal-review.evidence-height.v1',
    )).toBe('310'))
    expect(inspector.style.gridTemplateRows).toContain('310px')
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

  it('shows immediate feedback while the run request is still pending', async () => {
    let resolveStart: ((value: Response) => void) | undefined
    const pendingStart = new Promise<Response>(resolve => { resolveStart = resolve })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [] })
      if (url.includes('/api/signals/weekly-board-recognition/runs') && init?.method === 'POST') {
        return pendingStart
      }
      if (url.endsWith('/items')) return response({ items: [] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    const action = await screen.findByRole('button', { name: '运行本期信号' })
    await user.click(action)

    expect(screen.getByRole('status').textContent).toContain('正在创建本期任务')
    expect(action.textContent).toContain('正在创建本期任务')
    expect((action as HTMLButtonElement).disabled).toBe(true)

    resolveStart?.(await response({ ...run, status: 'running', phase: 'queued' }, 202))
    await vi.waitFor(() => expect(screen.getAllByText(/等待执行 100%/).length).toBeGreaterThan(0))
  })

  it('binds Codex chat to the selected signal result and its run', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [definition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [run] })
      if (url.endsWith('/items')) return response({ items })
      if (url.endsWith('/scores')) return response({ items: [] })
      if (url === '/api/ai/codex/status') return response({
        codex: { available: true, authenticated: true, experimental: true },
        templates: [], signal_templates: [{ id: 'signal-challenge', version: 'v1', label: '反例质疑', instruction: 'test' }],
      })
      if (url === '/api/ai/conversations' && init?.method === 'POST') return response(conversation, 201)
      if (url.startsWith('/api/ai/conversations?')) return response({ items: [{
        ...conversation, turn_count: 0, created_at_ms: 1, updated_at_ms: 1,
      }] })
      if (url === `/api/ai/conversations/${conversation.conversation_id}` && init?.method === 'DELETE') return new Response(null, { status: 204 })
      if (url === `/api/ai/conversations/${conversation.conversation_id}`) return response(conversation)
      if (url.endsWith('/turns') && init?.method === 'POST') return response({ turn_id: 'turn-1', status: 'queued' }, 202)
      throw new Error(`unexpected URL ${url}`)
    })
    class FakeEventSource {
      onerror: (() => void) | null = null
      listeners = new Map<string, (event: MessageEvent) => void>()
      constructor(public url: string) { eventSource = this }
      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        if (typeof listener === 'function') {
          this.listeners.set(type, listener as (event: MessageEvent) => void)
        }
      }
      emit(type: string, payload: Record<string, unknown>) {
        this.listeners.get(type)?.({ data: JSON.stringify(payload) } as MessageEvent)
      }
      close() {}
    }
    let eventSource: FakeEventSource | undefined
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
    await vi.waitFor(() => expect(eventSource).toBeDefined())
    eventSource?.emit('failed', { message: 'invalid MCP transport' })
    expect(await screen.findByText('invalid MCP transport')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '删除当前信号会话' }))
    expect(fetchMock.mock.calls.some(call =>
      String(call[0]).endsWith(conversation.conversation_id) && call[1]?.method === 'DELETE'
    )).toBe(true)
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
      if (url.endsWith('/scores')) return response({ items: [] })
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

  it('orders the complete board universe by opportunity score and keeps hard events visible', async () => {
    const dailyDefinition = {
      ...definition, signal_id: 'daily-market-board-review', name: '每日大盘与板块复盘',
      cadence: 'daily', profiles: ['market', 'attention'],
    }
    const dailyRun = { ...run, signal_id: dailyDefinition.signal_id, cadence: 'daily' }
    const observations = [
      dailyObservation('BK001.DC', '低分板块'),
      dailyObservation('BK002.DC', '高分板块'),
    ]
    const scores = [
      signalScore('BK001.DC', 58, false, 2, []),
      signalScore('BK002.DC', 82, true, 1, [{
        event_type: 'major-trend-breakout', direction: 'up', severity: 'high',
        state: 'new', source_code: '6m-descending-envelope-broken',
      }]),
    ]
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [dailyDefinition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [dailyRun] })
      if (url === '/api/signals/runs/run-0') return response({ ...dailyRun, run_id: 'run-0', revision: 1 })
      if (url.endsWith('/items')) return response({ items: [] })
      if (url.endsWith('/scores')) return response({ items: scores })
      if (url.endsWith('/attention')) return response({ items: [] })
      if (url.includes('/board-observations?')) return response({ items: observations, total: 2 })
      throw new Error(`unexpected URL ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click(await screen.findByRole('button', { name: /机会评分/ }))
    expect(await screen.findByText('硬异动 1')).toBeTruthy()
    expect(await screen.findByText('高分板块')).toBeTruthy()
    expect(screen.queryByText('低分板块')).toBeNull()
    expect(screen.getByText('82')).toBeTruthy()
    await user.click(screen.getByText('高分板块').closest('button')!)
    expect(screen.getByText('趋势机会')).toBeTruthy()
    expect(screen.getByText('固定算法摘要')).toBeTruthy()
    expect(screen.getByText('较上一轮增强')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /打开 2026-09-03 冻结评分/ }))
    expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/signals/runs/run-0')).toBe(true)
  })

  it('loads the exact M4 run and highlights the cited analysis item', async () => {
    const dailyDefinition = {
      ...definition, signal_id: 'daily-market-board-review', cadence: 'daily',
      profiles: ['market', 'attention'],
    }
    const dailyRun = { ...run, signal_id: dailyDefinition.signal_id, cadence: 'daily' }
    const dailyItem = {
      ...items[0], symbol: 'BK001.DC', name: '测试板块', profile: 'attention',
      payload: {
        rendered_summary: '固定结论', deep_analysis_run_id: 'deep-1',
        state_codes: [
          'bullish-boundary-triggered', 'descending-envelope-3m-broken',
          'descending-envelope-6m-broken', 'sudden-volume-expansion',
        ],
      },
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
      if (url.endsWith('/scores')) return response({ items: [] })
      if (url.endsWith('/attention')) return response({ items: [] })
      if (url === '/api/analysis/runs/deep-1') return response({ run_id: 'deep-1', items: [{
        item_id: 'scenario-1', item_type: 'scenario', payload: {
          kind: 'structural-trade-scenario', primary: true, rank: 1,
          direction: 'long', state: 'triggered', setup_family: 'triangle',
          horizon: 'medium', entry_price: 10, invalidation_price: 9,
          risk_percent: 10, selected_target_label: 'T1', has_trade_space: true,
          evidence_item_ids: ['line-1'], invalidation_evidence_item_ids: ['line-1'],
          targets: [{ label: 'T1', price: 12, basis: 'key-level',
            risk_reward_ratio: 2, stressed_risk_reward_ratio: 1.8,
            evidence_item_ids: ['line-1'] }],
        },
      }] })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click((await screen.findByText('BK001.DC')).closest('button')!)
    expect(document.querySelectorAll('.signal-result-details > span')).toHaveLength(3)
    expect(await screen.findByText('固定结论')).toBeTruthy()
    expect(screen.getByRole('region', { name: '盈亏比场景' })).toBeTruthy()
    expect(screen.getByTestId('signal-chart').dataset.analysisRun).toBe('deep-1')
    await user.click(screen.getByText('[S1]').closest('button')!)
    expect(screen.getByTestId('signal-chart').dataset.highlight).toBe('line-1')
  })

  it('opens the persisted stock pool with sources and its exact M4 chart', async () => {
    const dailyDefinition = {
      ...definition, signal_id: 'daily-market-board-review', cadence: 'daily',
      profiles: ['market', 'attention'],
    }
    const dailyRun = { ...run, signal_id: dailyDefinition.signal_id, cadence: 'daily' }
    const pool = {
      source_run_id: 'run-1', pool_kind: 'stock', effective_date: '2026-09-04',
      algorithm_version: 'stock-pool-v1', summary: { item_count: 1 }, created_at_ms: 1,
      items: [{
        symbol: '300001.SZ', name: '池内标的', kind: 'stock', exchange: 'SZ',
        lifecycle_state: 'strengthened', rank: 1,
        payload: {
          independent_score: 86, recognized: true,
          source_types: ['independent-strength', 'm4-analysis'],
          m4_analysis: { run_id: 'pool-m4-1', status: 'completed', warning_count: 0 },
          opportunity_classification: {
            classification: 'independent-opportunity', opportunity_eligible: true,
            credible_target_count: 2,
          },
        },
        sources: [{
          source_type: 'm4-analysis', source_reference: 'pool-m4-1',
          source_entity_key: 'scenario-1', reason: '独立强势且形成结构化交易场景',
          payload: { analysis_item_id: 'line-1' },
        }],
      }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/signals/definitions') return response({ items: [dailyDefinition] })
      if (url.includes('/api/signals/runs?')) return response({ items: [dailyRun] })
      if (url.endsWith('/items')) return response({ items: [] })
      if (url.endsWith('/scores')) return response({ items: [] })
      if (url.endsWith('/attention')) return response({ items: [] })
      if (url === '/api/observation-pools/runs/run-1/stock') return response(pool)
      if (url === '/api/analysis/runs/pool-m4-1') return response({
        run_id: 'pool-m4-1', items: [],
      })
      throw new Error(`unexpected URL ${url}`)
    }))
    const user = userEvent.setup()
    render(<SignalReviewWorkspace theme={themes[0]} onClose={() => undefined}/>)

    await user.click(await screen.findByRole('button', { name: /个股池/ }))
    await user.click((await screen.findByText('300001.SZ')).closest('button')!)
    expect(screen.getByTestId('signal-chart').textContent).toBe('300001.SZ')
    expect(screen.getByTestId('signal-chart').dataset.analysisRun).toBe('pool-m4-1')
    expect(screen.getByText('独立强势且形成结构化交易场景')).toBeTruthy()
    expect(screen.getByText('具备机会资格')).toBeTruthy()
    await user.click(screen.getByText('[O1]').closest('button')!)
    expect(screen.getByText('[O1]').closest('button')?.classList.contains('active')).toBe(true)
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

function dailyObservation(symbol: string, name: string) {
  return {
    run_id: 'run-1', symbol, name, exchange: 'DC', effective_date: '2026-09-04',
    coverage_state: 'complete', state_codes: [], metrics: {}, disqualifiers: [],
    attention_reasons: [], attention_eligible: false, conclusion_code: 'neutral',
    rendered_summary: '固定观察', comparison: { transition: 'new' },
    deep_analysis_state: 'completed-no-structural-evidence',
    deep_analysis_run_id: null, input_digest: `digest-${symbol}`,
    algorithm_version: 'daily-v1', config_version: 'config-v1',
  }
}

function signalScore(
  symbol: string, totalScore: number, eligible: boolean, rank: number,
  hardEvents: Array<Record<string, string>>,
) {
  return {
    run_id: 'run-1', entity_key: symbol, symbol, name: symbol, kind: 'sector',
    exchange: 'DC', effective_date: '2026-09-04', system_id: 'trend-breakout',
    scorer_version: 'trend-breakout-score-v1', entity_scope: 'board', eligible,
    total_score: totalScore, grade: totalScore >= 75 ? 'A' : 'C', rank,
    participant_count: 2, eligible_count: 1, verdict: '趋势机会',
    summary: '固定算法摘要', risk_summary: '固定风险摘要',
    change_summary: '较上一轮增强', stressed_risk_reward: eligible ? 3.8 : 2.2,
    components: {}, penalties: [], disqualifiers: eligible ? [] : ['below-3r'],
    hard_events: hardEvents, history: [{
      run_id: 'run-0', entity_key: symbol, effective_date: '2026-09-03', total_score: 70,
    }],
  }
}

function response(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}
