// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SignalReviewWorkspace } from './SignalReviewWorkspace'
import { themes } from './themeStore'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({ symbol }: { symbol: string }) => <div data-testid="signal-chart">{symbol}</div>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SignalReviewWorkspace', () => {
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

function response(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}
