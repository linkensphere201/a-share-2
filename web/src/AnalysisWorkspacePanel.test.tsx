// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalysisWorkspacePanel } from './AnalysisWorkspacePanel'
import type { TrendAnalysisRun } from './trendAnalysisClient'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const run: TrendAnalysisRun = {
  run_id: 'run-1', as_of_date: '2026-09-04', completion_state: 'complete',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'line-1', item_type: 'line', payload: {
      horizon: 'long', kind: 'resistance', score: 0.9,
      first_pivot_date: '2026-01-01', first_price: 20,
      second_pivot_date: '2026-06-01', second_price: 16,
      projected_price: 14, touch_count: 3, slope_per_bar: -0.03,
    },
  }],
}

describe('AnalysisWorkspacePanel', () => {
  it('places a result-bound Codex chat beside the shape-analysis result', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/ai/codex/status') return new Response(JSON.stringify({
        codex: { available: true, authenticated: true, experimental: true },
        templates: [{ id: 'correction', label: '纠错', instruction: '检查证据' }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url === '/api/ai/conversations') return new Response(JSON.stringify({
        conversation_id: 'conversation-1', symbol: '000001.SZ', timeframe: 'daily',
        source_run_id: 'run-1', as_of_date: '2026-09-04', algorithm_version: 'v1',
        config_version: 'c1', completion_state: 'complete', preview: false, turns: [],
      }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/ai/conversations?')) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/analysis/trend/')) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/analysis/ai/')) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      throw new Error(`unexpected URL ${url}`)
    }))

    render(<AnalysisWorkspacePanel
      symbol="000001.SZ"
      run={run}
      followingLatest
      onRunChange={vi.fn()}
      onHighlightItemChange={vi.fn()}
      onClose={vi.fn()}
    />)

    expect(screen.getByRole('dialog', { name: '形态分析结果与Codex对话' })).toBeTruthy()
    expect(screen.getByLabelText('形态分析结果')).toBeTruthy()
    expect(await screen.findByText('已连接')).toBeTruthy()
    expect(screen.getByRole('button', { name: '纠错' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '打开AI形态分析' })).toBeNull()
  })
})
