// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { App } from './App'
import { createDefaultWorkspace, workspaceStorageKey } from './workspace'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({
    symbol,
    volumeVisible,
    indicator,
    onVolumeVisibleChange,
    onIndicatorChange,
  }: {
    symbol: string
    volumeVisible: boolean
    indicator: 'macd' | 'none'
    onVolumeVisibleChange: (visible: boolean) => void
    onIndicatorChange: (indicator: 'macd' | 'none') => void
  }) => <div data-testid="chart-canvas">
    {symbol}
    {volumeVisible && <button aria-label="隐藏成交量栏" onClick={() => onVolumeVisibleChange(false)}/>}
    {indicator === 'macd' && <button aria-label="隐藏MACD栏" onClick={() => onIndicatorChange('none')}/>}
  </div>,
}))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('StockWorkspace', () => {
  it('opens the default list-plus-attached-chart group and collapses chat', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByText('表1')).toBeTruthy()
    expect(screen.getByTestId('chart-canvas').textContent).toBe('BK1128.DC')
    const chartWindow = screen.getByTestId('chart-canvas').closest('section')!
    expect(within(chartWindow).getByText('CPO概念')).toBeTruthy()
    expect(within(chartWindow).queryByText('表2')).toBeNull()
    expect(screen.getByText('2/8')).toBeTruthy()
    expect(screen.getByRole('button', { name: '刷新应用' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '编辑 表1 标的' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '编辑 CPO概念 标的' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '收起对话栏' }))
    expect(screen.getByRole('button', { name: '展开对话栏' })).toBeTruthy()
  })

  it('hides volume and MACD from their pane controls and persists both states', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '隐藏成交量栏' }))
    await user.click(screen.getByRole('button', { name: '隐藏MACD栏' }))

    expect(screen.queryByRole('button', { name: '隐藏成交量栏' })).toBeNull()
    expect(screen.queryByRole('button', { name: '隐藏MACD栏' })).toBeNull()
    expect(screen.getByTitle('显示成交量')).toBeTruthy()
    expect(screen.getByTitle('显示 MACD')).toBeTruthy()
    await waitFor(() => {
      const state = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(state.groups[0].windows[1].chart).toMatchObject({ volumeVisible: false, indicator: 'none' })
    })
  })

  it('adds a manual-list instrument and drives the attached chart', async () => {
    const instrument = { symbol: '510300.SH', name: '沪深300ETF', kind: 'etf', exchange: 'SH', rows: 3000 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [instrument] }) }))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '编辑 表1 标的' }))
    await user.type(screen.getByRole('textbox', { name: '搜索可添加标的' }), '沪深300')
    await user.click(await screen.findByRole('button', { name: /沪深300ETF/ }))
    await user.click(screen.getByRole('button', { name: '保存并退出' }))
    await user.click(screen.getByRole('button', { name: '选择 沪深300ETF' }))

    expect(screen.getByTestId('chart-canvas').textContent).toBe('510300.SH')
    await waitFor(() => {
      const state = JSON.parse(window.localStorage.getItem('stock-harness.workspace.v3') ?? '{}')
      expect(state).toMatchObject({
        version: 3,
        defaultGroupId: 'group-primary',
        groups: [{
          attachments: [{ type: 'show-symbol', sourceWindowId: 'list-primary', targetWindowId: 'chart-primary' }],
          windows: [
            { type: 'instrument-list', selectedSymbol: '510300.SH' },
            { type: 'chart', mode: 'attached', instrument },
          ],
        }],
      })
    })

    await user.click(screen.getByRole('button', { name: '编辑 表1 标的' }))
    await user.click(screen.getByRole('button', { name: '删除 沪深300ETF' }))
    await user.click(screen.getByRole('button', { name: '保存并退出' }))
    expect(screen.getByTestId('chart-canvas').textContent).toBe('BK1128.DC')
  })

  it('replaces the instrument of a detached chart through the unified editor', async () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows[1]
    if (chart.type !== 'chart') throw new Error('expected chart')
    chart.mode = 'detached'
    state.groups[0].attachments = []
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    const instrument = { symbol: '600519.SH', name: '贵州茅台', kind: 'stock', exchange: 'SH', rows: 5000 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [instrument] }) }))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '编辑 CPO概念 标的' }))
    await user.type(screen.getByRole('textbox', { name: '搜索可添加标的' }), '贵州茅台')
    await user.click(await screen.findByRole('button', { name: /贵州茅台/ }))
    await user.click(screen.getByRole('button', { name: '保存并退出' }))

    expect(screen.getByTestId('chart-canvas').textContent).toBe('600519.SH')
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(persisted.groups[0].windows[1]).toMatchObject({ mode: 'detached', instrument })
    })
  })

  it('maximizes and restores the list without rewriting the group layout', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    render(<App />)

    const before = JSON.stringify(JSON.parse(window.localStorage.getItem('stock-harness.workspace.v3') ?? '{}').groups[0].layout)
    await user.click(screen.getByRole('button', { name: '最大化 表1 窗口' }))
    expect(screen.queryByTestId('chart-canvas')).toBeNull()
    await user.click(screen.getByRole('button', { name: '还原窗口' }))
    expect(screen.getByTestId('chart-canvas')).toBeTruthy()
    const after = JSON.stringify(JSON.parse(window.localStorage.getItem('stock-harness.workspace.v3') ?? '{}').groups[0].layout)
    expect(after).toBe(before)
  })

  it('migrates legacy canvas state as a detached chart window', async () => {
    window.localStorage.setItem('stock-harness.workspace.v1', JSON.stringify([{
      id: 'canvas-old',
      instrument: { symbol: '000001.SZ', name: '平安银行', kind: 'stock', exchange: 'SZ', rows: 6000 },
      range: '10Y',
      priceMode: 'log',
      visibleRange: { from: '2016-01-01', to: '2026-01-01' },
    }]))
    vi.stubGlobal('fetch', emptyFetch())

    render(<App />)

    expect(screen.getByTestId('chart-canvas').textContent).toBe('000001.SZ')
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem('stock-harness.workspace.v3') ?? '{}')
      expect(persisted.groups[0]).toMatchObject({
        focusedWindowId: 'canvas-old',
        attachments: [],
        windows: [{ id: 'canvas-old', type: 'chart', mode: 'detached', chart: { range: '10Y', priceMode: 'log' } }],
      })
    })
  })

  it('creates and opens a persisted window group through layout management', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '布局管理' }))
    await user.click(screen.getByRole('button', { name: '新建窗口组' }))
    const name = screen.getByRole('textbox', { name: '窗口组名称' })
    await user.clear(name)
    await user.type(name, '医药观察')
    await user.selectOptions(screen.getByRole('combobox', { name: '窗口组模板' }), 'comparison')
    await user.click(screen.getByRole('button', { name: '创建' }))

    expect(screen.getByDisplayValue('医药观察')).toBeTruthy()
    expect(screen.getByText('3 个窗口')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '打开此组' }))
    expect((screen.getByRole('combobox', { name: '切换窗口组' }) as HTMLSelectElement).value).toMatch(/^group-/)
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem('stock-harness.workspace.v3') ?? '{}')
      expect(persisted.groups).toHaveLength(2)
      expect(persisted.groups[1]).toMatchObject({ name: '医药观察', windows: [{ type: 'instrument-list' }, { type: 'chart', mode: 'attached' }, { type: 'chart', mode: 'detached' }] })
    })
  })

  it('persists layout-editor ratios, window properties, and multi-driver settings', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '布局管理' }))
    const separator = screen.getByRole('separator', { name: '调整左右窗口比例' })
    expect(separator.getAttribute('aria-valuenow')).toBe('25')
    fireEvent.doubleClick(separator)

    const name = screen.getByRole('textbox', { name: '窗口名称' })
    await user.clear(name)
    await user.type(name, '主图')
    const mode = screen.getByRole('combobox', { name: '窗口属性' })
    await user.selectOptions(mode, 'detached')
    await user.selectOptions(mode, 'attached')
    const source = screen.getByRole('checkbox', { name: /表1/ })
    expect((source as HTMLInputElement).checked).toBe(false)
    await user.click(source)
    await user.click(screen.getByRole('button', { name: '打开此组' }))

    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(persisted.groups[0]).toMatchObject({
        layout: { ratio: 0.5 },
        windows: [{ title: '表1', mode: 'detached' }, { title: '主图', mode: 'attached' }],
        attachments: [{ sourceWindowId: 'list-primary', targetWindowId: 'chart-primary' }],
      })
    })
    expect(screen.getByRole('separator', { name: '调整左右窗口比例' }).getAttribute('aria-valuenow')).toBe('50')
  })

  it('drives a read-only member list, sorts it, and then drives the attached chart', async () => {
    const state = createDefaultWorkspace()
    const group = state.groups[0]
    const source = group.windows[0]
    if (source.type !== 'instrument-list') throw new Error('expected source list')
    source.selectedSymbol = undefined
    const derived = {
      id: 'list-members',
      type: 'instrument-list' as const,
      title: '成分列表',
      mode: 'attached' as const,
      content: { mode: 'manual' as const, instruments: [] },
    }
    group.windows.splice(1, 0, derived)
    group.layout = {
      id: 'split-root', type: 'split', direction: 'horizontal', ratio: 0.3,
      first: { id: 'layout-source', type: 'window', windowId: source.id },
      second: {
        id: 'split-right', type: 'split', direction: 'horizontal', ratio: 0.45,
        first: { id: 'layout-members', type: 'window', windowId: derived.id },
        second: { id: 'layout-chart', type: 'window', windowId: 'chart-primary' },
      },
    }
    group.attachments = [
      { id: 'edge-members', type: 'show-members', sourceWindowId: source.id, targetWindowId: derived.id },
      { id: 'edge-source-chart', type: 'show-symbol', sourceWindowId: source.id, targetWindowId: 'chart-primary' },
      { id: 'edge-chart', type: 'show-symbol', sourceWindowId: derived.id, targetWindowId: 'chart-primary' },
    ]
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      const items = url.includes('/members') ? [
        { symbol: '000001.SZ', name: '平安银行', kind: 'stock', exchange: 'SZ', rows: 6000, available: true, change_percent: -1.2, total_market_cap: 200_000_000_000 },
        { symbol: '600519.SH', name: '贵州茅台', kind: 'stock', exchange: 'SH', rows: 5000, available: true, change_percent: 2.5, total_market_cap: 1_800_000_000_000 },
      ] : []
      return Promise.resolve({ ok: true, json: async () => ({ items, as_of_date: '2026-08-03', source: 'eastmoney_board' }) })
    }))
    const user = userEvent.setup()

    render(<App />)
    expect(screen.getByText('上游列表尚未选择标的')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '选择 CPO概念' }))
    expect(await screen.findByRole('button', { name: '选择 平安银行' })).toBeTruthy()
    const derivedWindow = screen.getByText('成分列表').closest('section')!
    expect(within(derivedWindow).queryByRole('button', { name: '编辑 成分列表 标的' })).toBeNull()

    await user.click(within(derivedWindow).getByRole('button', { name: /涨跌幅/ }))
    await user.click(screen.getByRole('button', { name: '选择 贵州茅台' }))
    expect(screen.getByTestId('chart-canvas').textContent).toBe('600519.SH')
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(persisted.groups[0].windows[1]).toMatchObject({
        memberSourceWindowId: 'list-primary',
        selectedSymbol: '600519.SH',
        sort: { key: 'change_percent', direction: 'asc' },
      })
    })
  })

  it('creates a custom group and adds a searched member in the workspace modal', async () => {
    const instrument = { symbol: '300308.SZ', name: '中际旭创', kind: 'stock', exchange: 'SZ', rows: 3000 }
    let savedBody: Record<string, unknown> | undefined
    let created = false
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/market-snapshots')) return response({ items: [] })
      if (url.includes('/api/instruments?')) return response({ items: [instrument] })
      if (url.endsWith('/api/custom-groups') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        savedBody = body
        created = true
        return response({ id: 'group-1', symbol: 'CUSTOM:group-1', ...body })
      }
      if (url.endsWith('/api/custom-groups')) return response({
        items: created ? [{ id: 'group-1', symbol: 'CUSTOM:group-1', name: 'CPO自选', description: '', member_count: 1 }] : [],
      })
      if (url.endsWith('/api/custom-groups/group-1')) return response({
        id: 'group-1', symbol: 'CUSTOM:group-1', name: 'CPO自选', description: '',
        members: [{ ...instrument, tags: ['核心'], note: '' }],
      })
      return response({ items: [] })
    }))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '标的与自选集合' }))
    await user.click(await screen.findByRole('button', { name: '新建分组' }))
    const name = screen.getByRole('textbox', { name: '集合名' })
    await user.clear(name)
    await user.type(name, 'CPO自选')
    await user.type(screen.getByRole('textbox', { name: '搜索分组成员' }), '中际旭创')
    await user.click(await screen.findByRole('button', { name: /中际旭创/ }))
    await user.type(screen.getByRole('textbox', { name: '中际旭创 标签' }), '核心')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(savedBody).toMatchObject({
      name: 'CPO自选',
      members: [{ symbol: '300308.SZ', tags: ['核心'] }],
    }))
    expect((await screen.findAllByText('1 个标的')).length).toBeGreaterThanOrEqual(2)
  })
})

function emptyFetch() {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) })
}

function response(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body })
}
