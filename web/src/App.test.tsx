// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { App } from './App'
import { createDefaultWorkspace, defaultListColumns, workspaceStorageKey } from './workspace'

vi.mock('./ChartCanvas', () => ({
  ChartCanvas: ({
    symbol,
    volumeVisible,
    indicator,
    initialVisibleRange,
    onVisibleRangeChange,
    onVolumeVisibleChange,
    onIndicatorChange,
  }: {
    symbol: string
    volumeVisible: boolean
    indicator: 'macd' | 'none'
    initialVisibleRange?: { from: string; to: string }
    onVisibleRangeChange: (value: { from: string; to: string }) => void
    onVolumeVisibleChange: (visible: boolean) => void
    onIndicatorChange: (indicator: 'macd' | 'none') => void
  }) => <div data-testid="chart-canvas" data-visible-from={initialVisibleRange?.from} data-visible-to={initialVisibleRange?.to}>
    {symbol}
    <button aria-label="模拟缩放图表" onClick={() => onVisibleRangeChange({ from: '2025-04-01', to: '2026-08-05' })}/>
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
    expect(screen.getByRole('combobox', { name: '主题配色' }).querySelectorAll('option')).toHaveLength(20)
    expect(screen.getByRole('button', { name: '编辑 表1 标的' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '编辑 CPO概念 标的' })).toBeNull()
    expect(screen.getByRole('button', { name: '展开对话栏' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '展开对话栏' }))
    expect(screen.getByRole('button', { name: '收起对话栏' })).toBeTruthy()
  })

  it('switches and persists the workstation theme from the outer toolbar', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(screen.getByRole('combobox', { name: '主题配色' }), 'peachpuff')

    expect(window.localStorage.getItem('stock-harness.theme.v1')).toBe('peachpuff')
    expect(document.documentElement.dataset.theme).toBe('peachpuff')
    expect(document.documentElement.dataset.themeMode).toBe('light')
  })

  it('persists trend-system settings independently in the chart window', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    const first = render(<App />)

    await user.click(screen.getByRole('button', { name: '启用趋势交易体系' }))
    await user.click(screen.getByRole('button', { name: '仅查看趋势体系' }))
    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: '短期交易日' }), { target: { value: '80' } })
    await user.click(screen.getByRole('checkbox', { name: '显示关键位' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await user.click(screen.getByRole('button', { name: '收起趋势交易体系' }))

    await waitFor(() => {
      const state = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(state.groups[0].windows[1].chart.tradingSystems.trend).toMatchObject({
        enabled: true,
        expanded: false,
        isolate: true,
        settingsRevision: 1,
        settings: { shortHorizonBars: 80, mediumHorizonBars: 120, longHorizonBars: 250 },
        layers: { 'key-levels': false },
      })
    })

    first.unmount()
    render(<App />)
    expect(screen.getByRole('button', { name: '展开趋势交易体系' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '展开趋势交易体系' }))
    expect(screen.getByRole('button', { name: '停用趋势交易体系' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '退出趋势隔离' }).getAttribute('aria-pressed')).toBe('true')
    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    expect(screen.getByRole('spinbutton', { name: '短期交易日' })).toHaveProperty('value', '80')
    expect(screen.getByRole('checkbox', { name: '显示关键位' })).toHaveProperty('checked', false)
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

  it('persists each chart window visible range and restores it on reopen', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    const first = render(<App />)

    await user.click(screen.getByRole('button', { name: '模拟缩放图表' }))
    await waitFor(() => {
      const state = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(state.groups[0].windows[1].chart.visibleRange).toEqual({ from: '2025-04-01', to: '2026-08-05' })
    })

    first.unmount()
    render(<App />)
    expect(screen.getByTestId('chart-canvas').dataset).toMatchObject({
      visibleFrom: '2025-04-01',
      visibleTo: '2026-08-05',
    })
  })

  it('persists visible columns independently for each list window', async () => {
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()
    const first = render(<App />)

    await user.click(screen.getByRole('button', { name: '编辑 表1 表头' }))
    await user.click(screen.getByRole('checkbox', { name: '成交额' }))
    expect(screen.queryByRole('button', { name: /成交额/ })).toBeNull()
    await waitFor(() => {
      const state = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(state.groups[0].windows[0].visibleColumns).toEqual([
        'name', 'close', 'change_percent', 'volume', 'total_market_cap',
      ])
    })

    first.unmount()
    render(<App />)
    expect(screen.queryByRole('button', { name: /成交额/ })).toBeNull()
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

  it('routes an exact continuous futures selection into an attached chart', async () => {
    const state = createDefaultWorkspace()
    const list = state.groups[0].windows[0]
    if (list.type !== 'instrument-list') throw new Error('expected list')
    const future = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw', name: '沪铜主力',
      kind: 'futures-continuous', exchange: 'SHFE', rows: 5200,
      product_code: 'CU', series_kind: 'main', series_variant: 'MAIN',
    }
    list.content.instruments.push(future)
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    vi.stubGlobal('fetch', emptyFetch())
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '选择 沪铜主力' }))

    expect(screen.getByTestId('chart-canvas').textContent).toBe(future.symbol)
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(persisted.groups[0].windows[1]).toMatchObject({
        mode: 'attached', instrument: future,
      })
    })
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

  it('keeps an exact real futures contract in a detached chart', async () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows[1]
    if (chart.type !== 'chart') throw new Error('expected chart')
    chart.mode = 'detached'
    state.groups[0].attachments = []
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    const future = {
      symbol: 'FUT:SHFE:CU:202609', name: '沪铜2609',
      kind: 'futures-contract', exchange: 'SHFE', rows: 190,
      product_code: 'CU', lifecycle_status: 'trading', contract_month: '202609',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ items: [future] }),
    }))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '编辑 CPO概念 标的' }))
    await user.type(screen.getByRole('textbox', { name: '搜索可添加标的' }), '沪铜2609')
    await user.click(await screen.findByRole('button', { name: /沪铜2609/ }))
    await user.click(screen.getByRole('button', { name: '保存并退出' }))

    expect(screen.getByTestId('chart-canvas').textContent).toBe(future.symbol)
    await waitFor(() => {
      const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey) ?? '{}')
      expect(persisted.groups[0].windows[1]).toMatchObject({
        mode: 'detached', instrument: future,
      })
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
      visibleColumns: [...defaultListColumns],
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
        { symbol: '000001.SZ', name: '平安银行', kind: 'stock', exchange: 'SZ', rows: 6000, available: true, close: 10.5, volume: 100_000_000, amount: 1_050_000_000, change_percent: -1.2, total_market_cap: 200_000_000_000 },
        { symbol: '600519.SH', name: '贵州茅台', kind: 'stock', exchange: 'SH', rows: 5000, available: true, close: 1_420.5, volume: 2_000_000, amount: 2_840_000_000, change_percent: 2.5, total_market_cap: 1_800_000_000_000 },
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
    expect(within(derivedWindow).getByRole('button', { name: /价格/ })).toBeTruthy()
    expect(within(derivedWindow).getByRole('button', { name: /成交量/ })).toBeTruthy()
    expect(within(derivedWindow).getByRole('button', { name: /成交额/ })).toBeTruthy()

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
        members: [{ ...instrument, role: 'core_identity', tags: ['核心'], note: '' }],
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
    await user.selectOptions(screen.getByRole('combobox', { name: '中际旭创 角色' }), 'core_identity')
    await user.type(screen.getByRole('textbox', { name: '中际旭创 标签' }), '核心')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(savedBody).toMatchObject({
      name: 'CPO自选',
      members: [{ symbol: '300308.SZ', role: 'core_identity', tags: ['核心'] }],
    }))
    expect((await screen.findAllByText(/1 个标的/)).length).toBeGreaterThanOrEqual(2)
  })

  it('opens a custom-group mind map and drives the attached chart from a member node', async () => {
    const state = createDefaultWorkspace()
    const groupInstrument = {
      symbol: 'CUSTOM:group-ai', name: 'AI应用-企业软件核心', kind: 'custom-group',
      exchange: 'LOCAL', rows: 2,
    }
    const list = state.groups[0].windows[0]
    if (list.type !== 'instrument-list') throw new Error('expected list')
    list.content.instruments = [groupInstrument]
    list.selectedSymbol = groupInstrument.symbol
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input)
      if (url.endsWith('/api/custom-groups/group-ai')) return response({
        id: 'group-ai', name: groupInstrument.name, description: '企业AI应用观察池',
        members: [
          {
            symbol: '603039.SH', name: '泛微网络', kind: 'stock', exchange: 'SH',
            role: 'sentiment_anchor', tags: ['AI智能体'], note: '情绪锚点', available: true,
          },
          {
            symbol: '688111.SH', name: '金山办公', kind: 'stock', exchange: 'SH',
            role: 'bellwether', tags: ['AI办公'], note: '趋势中军', available: true,
          },
        ],
      })
      return response({ items: [] })
    }))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: `选择 ${groupInstrument.name}` }))

    const map = await screen.findByRole('dialog', { name: `板块分析 - ${groupInstrument.name}` })
    expect(within(map).getAllByText('情绪锚点')).toHaveLength(2)
    expect(within(map).getByText('容量锚点')).toBeTruthy()
    expect(within(map).getByText('中军')).toBeTruthy()
    expect(within(map).getByText('核心标识度')).toBeTruthy()
    expect(within(map).getByText('扩散补涨后排')).toBeTruthy()

    expect(within(map).queryByText('603039.SH')).toBeNull()
    expect(map.getAttribute('aria-modal')).toBe('true')
    expect(document.querySelector('.custom-group-map-interaction-shield')).toBeTruthy()
    expect(map.querySelector('strong')).toBeNull()
    expect(map.querySelector('header')).toBeNull()
    expect(map.querySelector('.custom-group-map-root')?.children).toHaveLength(2)
    const connector = document.querySelector('.custom-group-map-connector')!
    expect(connector).toBeTruthy()
    const line = connector.querySelector('line')!
    expect(Number(line.getAttribute('x2'))).toBe(Number.parseFloat(map.style.left))
    expect(Number(line.getAttribute('y2'))).toBe(Number.parseFloat(map.style.top) + Number.parseFloat(map.style.height) / 2)

    const initialLeft = map.style.left
    fireEvent.pointerDown(within(map).getByRole('button', { name: '移动板块分析窗口' }), { pointerId: 1, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 140, clientY: 130 })
    fireEvent.pointerUp(window, { pointerId: 1 })
    expect(map.style.left).not.toBe(initialLeft)

    const initialWidth = map.style.width
    fireEvent.pointerDown(within(map).getByRole('button', { name: '调整板块分析窗口大小' }), { pointerId: 2, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 40, clientY: 30 })
    fireEvent.pointerUp(window, { pointerId: 2 })
    expect(map.style.width).not.toBe(initialWidth)

    await user.click(within(map).getByRole('button', { name: /泛微网络/ }))
    expect(screen.getByTestId('chart-canvas').textContent).toBe('603039.SH')
    expect(document.querySelector('.custom-group-map-interaction-shield')).toBeNull()
    expect(screen.queryByRole('dialog', { name: `板块分析 - ${groupInstrument.name}` })).toBeNull()
  })
})

function emptyFetch() {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) })
}

function response(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body })
}
