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
  window.localStorage.clear()
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

  it('adds, focuses, maximizes, and restores instrument windows after reload', async () => {
    const instrument = {
      symbol: '510300.SH', name: '沪深300ETF', kind: 'etf', exchange: 'SH', rows: 3000,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [instrument] }),
    }))
    const user = userEvent.setup()

    const first = render(<App />)
    await user.click(await screen.findByRole('button', { name: '添加 沪深300ETF 窗口' }))
    expect(screen.getAllByTestId('chart-canvas')).toHaveLength(2)
    expect(screen.getByText('2/4')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: '最大化 沪深300ETF 窗口' }))
    expect(screen.getAllByTestId('chart-canvas')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: '还原窗口' }))

    first.unmount()
    render(<App />)
    expect(screen.getAllByTestId('chart-canvas')).toHaveLength(2)
    expect(JSON.parse(window.localStorage.getItem('stock-harness.workspace.v2') ?? '{}')).toMatchObject({
      version: 2,
      groups: [{ windows: [{ chart: { range: '3Y', priceMode: 'normal' } }, { instrument }] }],
    })
  })

  it('migrates legacy canvas state into the default window group', async () => {
    window.localStorage.setItem('stock-harness.workspace.v1', JSON.stringify([{
      id: 'canvas-old',
      instrument: { symbol: '000001.SZ', name: '平安银行', kind: 'stock', exchange: 'SZ', rows: 6000 },
      range: '10Y',
      priceMode: 'log',
      visibleRange: { from: '2016-01-01', to: '2026-01-01' },
    }]))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))

    render(<App />)

    expect(screen.getByTestId('chart-canvas').textContent).toBe('000001.SZ')
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem('stock-harness.workspace.v2') ?? '{}')
      expect(persisted.groups[0]).toMatchObject({
        id: 'group-primary',
        focusedWindowId: 'canvas-old',
        windows: [{ id: 'canvas-old', chart: { range: '10Y', priceMode: 'log' } }],
      })
    })
  })
})
