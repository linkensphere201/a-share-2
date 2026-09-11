// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LearningWorkspace } from './LearningWorkspace'

afterEach(() => vi.unstubAllGlobals())

describe('LearningWorkspace', () => {
  it('opens the published course inside StockHarness with a course-bound Codex pane', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/learning/systems') return response({ items: [{
        system_id: 'trend-genggui', title: '趋势交易体系', methodology: 'trend-trading',
        status: 'published', default: true, available: true,
        index_path: 'systems/trend-genggui/site/index.html',
        corpus_version: 'v1', publication_version: 'site-v1',
      }] })
      if (url === '/api/ai/codex/status') return response({
        codex: { available: true, authenticated: true, experimental: true },
        templates: [], learning_templates: [],
      })
      if (url === '/api/ai/conversations') return response(conversation(), 201)
      if (url.startsWith('/api/ai/conversations?')) return response({ items: [{
        conversation_id: 'chat-1', context_kind: 'learning_system',
        context_id: 'trend-genggui', source_run_id: null, title: '课程讨论',
        status: 'active', as_of_date: '2026-09-11', turn_count: 0,
        created_at_ms: 1, updated_at_ms: 1,
      }] })
      throw new Error(`unexpected fetch ${url}`)
    }))

    render(<LearningWorkspace onClose={() => {}}/>)

    expect(await screen.findByText('交易系统学习')).toBeTruthy()
    expect((await screen.findByTitle('趋势交易体系')).getAttribute('src')).toBe(
      '/learning/systems/trend-genggui/site/index.html',
    )
    await waitFor(() => expect(screen.getByText('Codex 课程讨论')).toBeTruthy())
    expect(screen.getByLabelText('课程讨论输入')).toBeTruthy()
  })
})

function response(payload: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload } as Response
}

function conversation() {
  return {
    conversation_id: 'chat-1', context_kind: 'learning_system', context_id: 'trend-genggui',
    symbol: null, timeframe: null, source_run_id: null, as_of_date: '2026-09-11',
    algorithm_version: 'learning-visible-text-v1', config_version: 'site-v1',
    completion_state: 'complete', preview: false, title: '课程讨论', status: 'active', turns: [],
  }
}
