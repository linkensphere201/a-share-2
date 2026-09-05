// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalysisChatPanel } from './AnalysisChatPanel'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const run: TrendAnalysisRun = {
  run_id: 'run-1', as_of_date: '2026-09-05', completion_state: 'completed',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'zone-1', item_type: 'zone', payload: {},
  }],
}

const conversation = {
  conversation_id: 'conversation-1', symbol: '000001.SZ', timeframe: 'daily',
  source_run_id: 'run-1', as_of_date: '2026-09-05', algorithm_version: 'v1',
  config_version: 'c1', completion_state: 'completed', preview: false,
  title: '000001.SZ', status: 'active', turns: [],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AnalysisChatPanel', () => {
  it('renders the submitted prompt and working state before turn creation completes', async () => {
    const pendingTurn = new Promise<Response>(() => undefined)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/ai/codex/status') return jsonResponse({
        codex: { available: true, authenticated: true, experimental: true }, templates: [],
      })
      if (url === '/api/ai/conversations' && init?.method === 'POST') return jsonResponse(conversation, 201)
      if (url.startsWith('/api/ai/conversations?')) return jsonResponse({ items: [conversation] })
      if (url.endsWith('/turns') && init?.method === 'POST') return pendingTurn
      throw new Error(`unexpected URL ${url}`)
    }))

    const view = render(<AnalysisChatPanel
      symbol="000001.SZ"
      run={run}
      onHighlightItemChange={vi.fn()}
      onCollapse={vi.fn()}
    />)
    await screen.findByText('已连接')
    const textarea = view.container.querySelector('textarea')!
    fireEvent.change(textarea, { target: { value: '**关键位**与*趋势*\n- 支撑 [K1]\n- 等待确认' } })
    fireEvent.click(view.container.querySelector('.analysis-chat-input > button')!)

    expect(await screen.findByText('Working')).toBeTruthy()
    expect(screen.getByText('已发送', { exact: false })).toBeTruthy()
    expect(screen.getByText('关键位').parentElement?.tagName).toBe('STRONG')
    expect(screen.getByText('趋势').parentElement?.tagName).toBe('EM')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '[K1]' })).toBeTruthy()
    expect(textarea.value).toBe('')
    expect(textarea.disabled).toBe(true)
  })

  it('shows allowlisted MCP tool progress from the turn event stream', async () => {
    const sources: FakeEventSource[] = []
    vi.stubGlobal('EventSource', class extends FakeEventSource {
      constructor(url: string) {
        super(url)
        sources.push(this)
      }
    })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/ai/codex/status') return jsonResponse({
        codex: {
          available: true, authenticated: true, experimental: true,
          mcp_enabled: true, mcp_server: 'stock_harness_embedded', mcp_tools: ['get_daily_bars'],
        },
        templates: [],
      })
      if (url === '/api/ai/conversations' && init?.method === 'POST') return jsonResponse(conversation, 201)
      if (url.startsWith('/api/ai/conversations?')) return jsonResponse({ items: [conversation] })
      if (url.endsWith('/turns') && init?.method === 'POST') return jsonResponse({ turn_id: 'turn-1', status: 'queued' }, 202)
      throw new Error(`unexpected URL ${url}`)
    }))

    const view = render(<AnalysisChatPanel
      symbol="000001.SZ" run={run}
      onHighlightItemChange={vi.fn()} onCollapse={vi.fn()}
    />)
    await screen.findByText('已连接')
    fireEvent.change(view.container.querySelector('textarea')!, {
      target: { value: '比较另一个标的' },
    })
    fireEvent.click(view.container.querySelector('.analysis-chat-input > button')!)
    await waitFor(() => expect(sources).toHaveLength(1))

    sources[0].emit('tool-started', {
      item_id: 'tool-1', server: 'stock_harness_embedded', tool: 'get_daily_bars',
      status: 'inProgress', arguments: { symbol: '002602.SZ' },
    })
    expect(await screen.findByText('读取日线')).toBeTruthy()
    expect(screen.getByText('002602.SZ')).toBeTruthy()
    expect(screen.getByText('运行中')).toBeTruthy()

    sources[0].emit('tool-completed', {
      item_id: 'tool-1', server: 'stock_harness_embedded', tool: 'get_daily_bars',
      status: 'completed', duration_ms: 18, arguments: { symbol: '002602.SZ' },
    })
    expect(await screen.findByText('完成 · 18ms')).toBeTruthy()
  })
})

class FakeEventSource {
  onerror: (() => void) | null = null
  private listeners = new Map<string, Array<(event: MessageEvent) => void>>()

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as (event: MessageEvent) => void
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback])
  }

  emit(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  close() {}
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}
