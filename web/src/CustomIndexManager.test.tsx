// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CustomIndexManager } from './CustomIndexManager'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('CustomIndexManager', () => {
  it('creates an equal-weight materialized index from exact stock selection', async () => {
    let created = false
    let submitted: Record<string, unknown> | undefined
    const detail = {
      id: 'index-1', symbol: 'CINDEX:index-1', name: '自定义指数1', description: '',
      base_date: '2016-08-09', base_value: 1000, effective_from: '2016-08-09',
      weighting_method: 'equal', revision_number: 1, status: 'ready', rows: 2,
      members: [{
        symbol: '300308.SZ', name: '中际旭创', raw_weight: 1, normalized_weight: 1,
      }],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/instruments?')) return response({
        items: [{
          symbol: '300308.SZ', name: '中际旭创', kind: 'stock', exchange: 'SZ',
          rows: 1000, first_trade_date: '2015-04-22',
        }],
        has_more: false, next_offset: 1,
      })
      if (url === '/api/custom-indices' && init?.method === 'POST') {
        submitted = JSON.parse(String(init.body)) as Record<string, unknown>
        created = true
        return response(detail)
      }
      if (url === '/api/custom-indices') return response({
        items: created ? [{ ...detail, member_count: 1 }] : [],
      })
      if (url === '/api/custom-indices/index-1') return response(detail)
      throw new Error(`unexpected request: ${url}`)
    }))
    const user = userEvent.setup()

    render(<CustomIndexManager onClose={() => undefined}/>)
    await user.click(await screen.findByRole('button', { name: '新建指数' }))
    await user.click(await screen.findByRole('button', { name: /中际旭创/ }))
    await user.click(screen.getByRole('button', { name: '回算并保存' }))

    await waitFor(() => expect(submitted).toBeTruthy())
    expect(submitted).toMatchObject({
      name: '自定义指数1', base_date: '2015-04-22', base_value: 1000,
      weighting_method: 'equal', members: [{ symbol: '300308.SZ' }],
    })
    expect(await screen.findByText('2 根已物化日线')).toBeTruthy()
  })
})

function response(body: object) {
  return { ok: true, status: 200, json: async () => body }
}
