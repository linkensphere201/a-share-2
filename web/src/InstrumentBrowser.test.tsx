// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InstrumentBrowser } from './InstrumentBrowser'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('InstrumentBrowser', () => {
  it('browses a classified board list without requiring a keyword', async () => {
    const concept = {
      symbol: 'BK0475.DC', name: '半导体', kind: 'sector', exchange: 'DC', rows: 1000,
      classification: 'concept', classification_label: '概念板块', source_label: '东财',
    }
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      requests.push(url)
      const items = url.includes('classification=concept') ? [concept] : []
      return Promise.resolve({ ok: true, json: async () => ({ items, has_more: false, next_offset: items.length }) })
    }))
    const onSelect = vi.fn()
    const user = userEvent.setup()

    render(<InstrumentBrowser
      selectedSymbols={new Set()}
      onSelect={onSelect}
      searchLabel="搜索可添加标的"
      placeholder="搜索"
    />)
    await user.click(screen.getByRole('tab', { name: '概念板块' }))

    expect(await screen.findByText('半导体')).toBeTruthy()
    expect(screen.getByText('东财')).toBeTruthy()
    expect(requests.some(url => url.includes('classification=concept') && url.includes('query='))).toBe(true)
    await user.click(screen.getByRole('button', { name: /半导体/ }))
    expect(onSelect).toHaveBeenCalledWith(concept)
  })

  it('keeps keyword search inside the selected classification and loads more', async () => {
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      requests.push(url)
      const offset = new URL(url, 'http://local').searchParams.get('offset')
      const items = offset === '80'
        ? [{ symbol: '801010.SI', name: '农林牧渔', kind: 'sector', exchange: 'SI', rows: 1000, classification_label: '行业板块', source_label: '申万' }]
        : [{ symbol: 'BK0475.DC', name: '半导体', kind: 'sector', exchange: 'DC', rows: 1000, classification_label: '行业板块', source_label: '东财' }]
      return Promise.resolve({ ok: true, json: async () => ({ items, has_more: offset !== '80', next_offset: offset === '80' ? 81 : 80 }) })
    }))
    const user = userEvent.setup()

    render(<InstrumentBrowser
      selectedSymbols={new Set()}
      onSelect={() => undefined}
      searchLabel="搜索可添加标的"
      placeholder="搜索"
    />)
    await user.click(screen.getByRole('tab', { name: '行业板块' }))
    const search = screen.getByRole('textbox', { name: '搜索可添加标的' })
    await user.type(search, 'bdt')
    await waitFor(() => expect(requests.some(url => url.includes('classification=industry') && url.includes('query=bdt'))).toBe(true))
    await user.click(await screen.findByRole('button', { name: '加载更多' }))

    expect(await screen.findByText('农林牧渔')).toBeTruthy()
    expect(requests.some(url => url.includes('classification=industry') && url.includes('offset=80'))).toBe(true)
  })

  it('ignores a stale load-more response after the query changes', async () => {
    let resolveStale!: (value: { ok: boolean; json: () => Promise<unknown> }) => void
    const stalePage = new Promise<{ ok: boolean; json: () => Promise<unknown> }>(resolve => {
      resolveStale = resolve
    })
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = new URL(String(input), 'http://local')
      const query = url.searchParams.get('query')
      const offset = url.searchParams.get('offset')
      if (query === '' && offset === '80') return stalePage
      const items = query === 'new'
        ? [{ symbol: 'NEW.SZ', name: 'NEW', kind: 'stock', exchange: 'SZ', rows: 1 }]
        : [{ symbol: 'OLD.SZ', name: 'OLD', kind: 'stock', exchange: 'SZ', rows: 1 }]
      return Promise.resolve({
        ok: true,
        json: async () => ({ items, has_more: query === '', next_offset: 80 }),
      })
    }))
    const user = userEvent.setup()

    render(<InstrumentBrowser
      selectedSymbols={new Set()}
      onSelect={() => undefined}
      searchLabel="Search instruments"
      placeholder="Search"
    />)
    expect(await screen.findByText('OLD')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /加载更多/ }))
    await user.type(screen.getByRole('textbox', { name: 'Search instruments' }), 'new')
    expect(await screen.findByText('NEW')).toBeTruthy()

    resolveStale({
      ok: true,
      json: async () => ({
        items: [{ symbol: 'STALE.SZ', name: 'STALE', kind: 'stock', exchange: 'SZ', rows: 1 }],
        has_more: false,
        next_offset: 81,
      }),
    })
    await waitFor(() => expect(screen.queryByText('STALE')).toBeNull())
  })
})
