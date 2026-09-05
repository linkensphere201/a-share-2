// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InstrumentTagManager } from './InstrumentTagManager'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('InstrumentTagManager', () => {
  it('loads, edits, and persists global stock tags', async () => {
    const updates: string[] = []
    const requests: Array<{ url: string; method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.startsWith('/api/instruments?')) return response({
        items: [{
          symbol: '300308.SZ', name: '中际旭创', kind: 'stock', exchange: 'SZ',
          rows: 1000, instrument_tags: ['板块核心辨识度'],
        }],
        has_more: false,
        next_offset: 1,
      })
      if (url === '/api/instruments/300308.SZ' && method === 'GET') return response({
        symbol: '300308.SZ', name: '中际旭创', kind: 'stock', exchange: 'SZ',
        rows: 1000, instrument_tags: ['板块核心辨识度'],
      })
      if (url === '/api/instruments/300308.SZ/tags' && method === 'PUT') {
        const tags = (JSON.parse(String(init?.body)) as { tags: string[] }).tags
        return response({ symbol: '300308.SZ', tags })
      }
      throw new Error(`unexpected request ${method} ${url}`)
    }))
    window.addEventListener('stock-harness:instrument-tags-changed', event => {
      updates.push((event as CustomEvent<{ symbol: string }>).detail.symbol)
    }, { once: true })
    const user = userEvent.setup()

    render(<InstrumentTagManager/>)
    await user.click(await screen.findByRole('button', { name: /中际旭创/ }))
    expect(await screen.findByText('板块核心辨识度', { selector: '.instrument-tag-current span' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '情绪弹性核心' }))
    await user.type(screen.getByRole('textbox', { name: '自定义标的标签' }), 'CPO核心')
    await user.click(screen.getByRole('button', { name: '添加' }))
    await user.click(screen.getByRole('button', { name: '保存标签' }))

    await waitFor(() => expect(updates).toEqual(['300308.SZ']))
    expect(requests).toContainEqual({ url: '/api/instruments/300308.SZ/tags', method: 'PUT' })
    expect(screen.getByText('3/8 · 已保存')).toBeTruthy()
  })
})

function response(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}
