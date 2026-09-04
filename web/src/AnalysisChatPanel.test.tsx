// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
})

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}
