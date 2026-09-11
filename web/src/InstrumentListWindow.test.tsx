// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { InstrumentListWindow } from './InstrumentListWindow'
import type { InstrumentListWindowState } from './workspace'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('InstrumentListWindow instrument tags', () => {
  it('shows persisted global tags as compact labels', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.startsWith('/api/market-snapshots?')) return response({ items: [] })
      if (url.startsWith('/api/instrument-tags?')) return response({
        items: [{
          symbol: '002708.SZ',
          tags: ['板块核心辨识度', '情绪弹性核心'],
        }],
      })
      if (url.startsWith('/api/instrument-board-tags?')) return response({
        items: [{ symbol: '002708.SZ', tags: [{
          board_symbol: 'BK0816.DC', name: 'Auto Parts', classification: 'industry',
          position: 0, source_system: 'eastmoney', selection_score: 90,
          selection_reason: 'structured-industry:2', algorithm_version: 'stock-board-tags-v1',
          generated_at_ms: 1,
        }] }],
      })
      throw new Error(`unexpected request ${url}`)
    }))
    const windowState: InstrumentListWindowState = {
      id: 'list-1', type: 'instrument-list', title: '观察列表', mode: 'detached',
      presentation: { mode: 'docked' },
      content: {
        mode: 'manual',
        instruments: [{
          symbol: '002708.SZ', name: '光洋股份', kind: 'stock', exchange: 'SZ', rows: 1000,
        }],
      },
      visibleColumns: ['name'],
    }

    render(<InstrumentListWindow
      windowState={windowState}
      focused
      maximized={false}
      removable
      onFocus={() => undefined}
      onToggleMaximize={() => undefined}
      onRemoveWindow={() => undefined}
      onSelect={() => undefined}
      onDeleteInstrument={() => undefined}
      onTemporaryCast={() => undefined}
      chartTargets={[]}
      onEdit={() => undefined}
      onPopOut={() => undefined}
      onDock={() => undefined}
      derived={false}
      onSortChange={() => undefined}
      onVisibleColumnsChange={() => undefined}
      onReferencedSymbolsChange={() => undefined}
    />)

    expect(await screen.findByText('板块核心辨识度')).toBeTruthy()
    expect(screen.getByText('情绪弹性核心')).toBeTruthy()
    expect(screen.getByText('行业·Auto Parts')).toBeTruthy()
  })

  it('shows the stock market-board badge beside the instrument name', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.startsWith('/api/market-snapshots?')) return response({ items: [] })
      if (url.startsWith('/api/instrument-tags?')) return response({ items: [] })
      if (url.startsWith('/api/instrument-board-tags?')) return response({ items: [] })
      throw new Error(`unexpected request ${url}`)
    }))
    const windowState: InstrumentListWindowState = {
      id: 'list-1', type: 'instrument-list', title: '观察列表', mode: 'detached',
      presentation: { mode: 'docked' },
      content: {
        mode: 'manual',
        instruments: [{
          symbol: '688519.SH', name: '南亚新材', kind: 'stock', exchange: 'SH', rows: 1000,
        }],
      },
      visibleColumns: ['name'],
    }

    render(<InstrumentListWindow
      windowState={windowState}
      focused
      maximized={false}
      removable
      onFocus={() => undefined}
      onToggleMaximize={() => undefined}
      onRemoveWindow={() => undefined}
      onSelect={() => undefined}
      onDeleteInstrument={() => undefined}
      onTemporaryCast={() => undefined}
      chartTargets={[]}
      onEdit={() => undefined}
      onPopOut={() => undefined}
      onDock={() => undefined}
      derived={false}
      onSortChange={() => undefined}
      onVisibleColumnsChange={() => undefined}
      onReferencedSymbolsChange={() => undefined}
    />)

    expect(await screen.findByLabelText('科创板')).toBeTruthy()
  })

  it('offers fixed-list deletion and temporary chart casting from the row menu', () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.startsWith('/api/market-snapshots?')) return response({ items: [] })
      if (url.startsWith('/api/instrument-tags?')) return response({ items: [] })
      if (url.startsWith('/api/instrument-board-tags?')) return response({ items: [] })
      throw new Error(`unexpected request ${url}`)
    }))
    const instrument = {
      symbol: '002708.SZ', name: '光洋股份', kind: 'stock' as const,
      exchange: 'SZ', rows: 1000,
    }
    const windowState: InstrumentListWindowState = {
      id: 'list-1', type: 'instrument-list', title: '观察列表', mode: 'detached',
      presentation: { mode: 'docked' },
      content: { mode: 'manual', instruments: [instrument] },
      visibleColumns: ['name'],
    }
    const onDeleteInstrument = vi.fn()
    const onTemporaryCast = vi.fn()

    render(<InstrumentListWindow
      windowState={windowState}
      focused
      maximized={false}
      removable
      onFocus={() => undefined}
      onToggleMaximize={() => undefined}
      onRemoveWindow={() => undefined}
      onSelect={() => undefined}
      onDeleteInstrument={onDeleteInstrument}
      onTemporaryCast={onTemporaryCast}
      chartTargets={[{ id: 'chart-1', title: '主图', instrumentName: '上证指数' }]}
      onEdit={() => undefined}
      onPopOut={() => undefined}
      onDock={() => undefined}
      derived={false}
      onSortChange={() => undefined}
      onVisibleColumnsChange={() => undefined}
      onReferencedSymbolsChange={() => undefined}
    />)

    const row = screen.getByRole('button', { name: '选择 光洋股份' }).closest('.list-window-row')!
    fireEvent.contextMenu(row, { clientX: 100, clientY: 100 })
    expect(screen.getByRole('menuitem', { name: '从当前列表删除' })).toBeTruthy()
    fireEvent.click(screen.getByRole('menuitem', { name: /临时投屏至/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /主图/ }))
    expect(onTemporaryCast).toHaveBeenCalledWith('chart-1', instrument)

    fireEvent.contextMenu(row, { clientX: 100, clientY: 100 })
    fireEvent.click(screen.getByRole('menuitem', { name: '从当前列表删除' }))
    expect(onDeleteInstrument).toHaveBeenCalledWith(instrument)
  })
})

function response(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}
