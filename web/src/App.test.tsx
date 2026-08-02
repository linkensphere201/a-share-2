// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { App } from './App'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({ symbol }: { symbol: string }) => <div data-testid="chart-canvas">{symbol}</div>,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('App shell', () => {
  it('filters instruments by kind and collapses chat', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<App />)
    await screen.findByText('没有匹配标的')

    await user.click(screen.getByRole('button', { name: '板块' }))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenLastCalledWith(
        expect.stringContaining('kind=sector'),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })

    await user.click(screen.getByRole('button', { name: '收起对话栏' }))
    expect(screen.getByRole('button', { name: '展开对话栏' })).toBeTruthy()
  })
})
