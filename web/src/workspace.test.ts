// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  appendInstrumentToManualList,
  createDefaultWorkspace,
  createWindowGroup,
  chartRanges,
  duplicateWindowGroup,
  deriveReferencedSymbols,
  isChartableInstrument,
  legacyWorkspaceStorageKey,
  loadWorkspace,
  previousWorkspaceStorageKey,
  saveWorkspace,
  workspaceStorageKey,
} from './workspace'
import { createTradingSystemWindowStates } from './tradingSystems'

afterEach(() => window.localStorage.clear())

describe('workspace persistence', () => {
  it('normalizes and persists native window presentation geometry', () => {
    const state = createDefaultWorkspace()
    state.groups[0].windows[0].presentation = {
      mode: 'popped-out',
      geometry: { x: 120, y: 80, width: 1100, height: 760 },
    }
    saveWorkspace(state)

    expect(loadWorkspace().groups[0].windows[0].presentation).toEqual({
      mode: 'popped-out',
      geometry: { x: 120, y: 80, width: 1100, height: 760 },
    })
  })

  it('defaults legacy windows to docked and rejects unusable geometry', () => {
    const state = createDefaultWorkspace()
    const stored = structuredClone(state) as unknown as {
      groups: Array<{ windows: Array<Record<string, unknown>> }>
    }
    delete stored.groups[0].windows[0].presentation
    stored.groups[0].windows[1].presentation = {
      mode: 'popped-out', geometry: { x: 0, y: 0, width: 20, height: 20 },
    }
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(stored))

    const restored = loadWorkspace().groups[0].windows
    expect(restored[0].presentation).toEqual({ mode: 'docked' })
    expect(restored[1].presentation).toEqual({ mode: 'popped-out' })
  })

  it('appends a screener instrument only to writable manual lists and persists it', () => {
    const state = createDefaultWorkspace()
    const group = state.groups[0]
    const list = group.windows.find(item => item.type === 'instrument-list')!
    if (list.type !== 'instrument-list') throw new Error('expected list')
    const candidate = {
      symbol: '000001.SZ', name: '平安银行', kind: 'stock', exchange: 'SZ', rows: 0,
    }

    const appended = appendInstrumentToManualList(group, list.id, candidate)
    const duplicate = appendInstrumentToManualList(appended.group, list.id, candidate)
    const attachedGroup = {
      ...appended.group,
      windows: appended.group.windows.map(item => item.id === list.id
        ? { ...list, mode: 'attached' as const }
        : item),
    }
    const attached = appendInstrumentToManualList(attachedGroup, list.id, {
      symbol: '000002.SZ', name: '万科A', kind: 'stock', exchange: 'SZ', rows: 0,
    })

    expect(appended.added).toBe(true)
    expect(duplicate.added).toBe(false)
    expect(attached.added).toBe(false)
    saveWorkspace({ ...state, groups: [appended.group] })
    const restored = loadWorkspace().groups[0].windows.find(item => item.id === list.id)
    expect(restored?.type === 'instrument-list'
      ? restored.content.instruments.map(item => item.symbol)
      : []).toContain(candidate.symbol)
  })

  it('rejects futures product catalog nodes from persisted list and chart targets', () => {
    const state = createDefaultWorkspace()
    const list = state.groups[0].windows.find(item => item.type === 'instrument-list')!
    if (list.type !== 'instrument-list') throw new Error('expected list')
    const product = {
      symbol: 'FUTPROD:SHFE:CU', name: '沪铜', kind: 'futures-product',
      exchange: 'SHFE', rows: 0,
    }
    list.content.instruments.push(product)
    saveWorkspace(state)

    const restored = loadWorkspace().groups[0].windows.find(
      item => item.type === 'instrument-list'
    )
    expect(restored).toMatchObject({
      content: { instruments: expect.not.arrayContaining([product]) },
    })
    expect(isChartableInstrument(product)).toBe(false)
  })

  it('persists selectable futures list columns and sorting', () => {
    const state = createDefaultWorkspace()
    const list = state.groups[0].windows.find(item => item.type === 'instrument-list')!
    if (list.type !== 'instrument-list') throw new Error('expected list')
    list.visibleColumns = [
      'name', 'close', 'settlement_change_percent', 'open_interest',
      'open_interest_change', 'source_state', 'contract_month', 'last_trading_date',
    ]
    list.sort = { key: 'open_interest_change', direction: 'desc' }

    saveWorkspace(state)
    const restored = loadWorkspace().groups[0].windows.find(
      item => item.type === 'instrument-list'
    )

    expect(restored).toMatchObject({
      visibleColumns: list.visibleColumns,
      sort: list.sort,
    })
  })

  it('persists real and continuous futures targets with their metadata', () => {
    const state = createDefaultWorkspace()
    const list = state.groups[0].windows.find(item => item.type === 'instrument-list')!
    const chart = state.groups[0].windows.find(item => item.type === 'chart')!
    if (list.type !== 'instrument-list' || chart.type !== 'chart') throw new Error('expected windows')
    const contract = {
      symbol: 'FUT:SHFE:CU:202609', name: '沪铜2609', kind: 'futures-contract',
      exchange: 'SHFE', rows: 200, product_code: 'CU', lifecycle_status: 'trading',
      contract_month: '202609',
    }
    const continuous = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw', name: '沪铜主力', kind: 'futures-continuous',
      exchange: 'SHFE', rows: 5000, product_code: 'CU', series_kind: 'main',
      series_variant: 'MAIN',
    }
    list.content.instruments = [contract]
    chart.instrument = continuous

    saveWorkspace(state)
    const restored = loadWorkspace()

    expect(restored.groups[0].windows).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: 'instrument-list',
        content: expect.objectContaining({ instruments: [contract] }),
      }),
      expect.objectContaining({ type: 'chart', instrument: continuous }),
    ]))
  })

  it('persists futures settlement, open-interest, and pane ratio state', () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows.find(item => item.type === 'chart')!
    if (chart.type !== 'chart') throw new Error('expected chart')
    chart.chart.settlementVisible = true
    chart.chart.openInterestVisible = true
    chart.chart.drawingToolbarCollapsed = true
    chart.chart.paneRatios = { price: 0.55, volume: 0.15, macd: 0.12, openInterest: 0.18 }

    saveWorkspace(state)
    const restored = loadWorkspace().groups[0].windows.find(item => item.type === 'chart')

    expect(restored).toMatchObject({
      chart: {
        settlementVisible: true,
        openInterestVisible: true,
        drawingToolbarCollapsed: true,
        paneRatios: chart.chart.paneRatios,
      },
    })
  })

  it('derives and deduplicates every active group window reference', () => {
    const state = createDefaultWorkspace()
    const group = state.groups[0]
    const list = group.windows.find(item => item.type === 'instrument-list')!
    if (list.type !== 'instrument-list') throw new Error('expected list')
    list.content.instruments.push(instrument('510300.SH'))

    expect(deriveReferencedSymbols(group, {
      [list.id]: ['300308.SZ', '510300.SH'],
    })).toEqual(['300308.SZ', '510300.SH', 'BK1128.DC'])
  })

  it('derives direct, custom-group, and resolved futures references once', () => {
    const state = createDefaultWorkspace()
    const group = state.groups[0]
    const list = group.windows.find(item => item.type === 'instrument-list')!
    const chart = group.windows.find(item => item.type === 'chart')!
    if (list.type !== 'instrument-list' || chart.type !== 'chart') throw new Error('expected windows')
    const continuous = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw', name: '沪铜主力',
      kind: 'futures-continuous', exchange: 'SHFE', rows: 5200,
    }
    list.content.instruments = [{
      symbol: 'CUSTOM:mixed', name: '混合集合', kind: 'custom-group',
      exchange: 'LOCAL', rows: 3,
    }, continuous]
    chart.instrument = continuous

    expect(deriveReferencedSymbols(group, {
      [list.id]: ['300308.SZ', continuous.symbol, 'FUT:SHFE:CU:202609'],
    })).toEqual([
      '300308.SZ', 'CUSTOM:mixed', 'FUT:SHFE:CU:202609', continuous.symbol,
    ])
  })

  it('accepts the one-month chart range', () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows.find(item => item.type === 'chart')!
    if (chart.type === 'chart') chart.chart.range = '1M'
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))

    expect(chartRanges[0]).toBe('1M')
    expect(loadWorkspace().groups[0].windows.find(item => item.type === 'chart')).toMatchObject({
      chart: { range: '1M' },
    })
  })

  it('migrates missing indicators to MACD and preserves an explicit hidden state', () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows.find(item => item.type === 'chart')!
    if (chart.type !== 'chart') throw new Error('expected chart')
    delete (chart.chart as Partial<typeof chart.chart>).indicator
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    expect(loadWorkspace().groups[0].windows.find(item => item.type === 'chart')).toMatchObject({
      chart: { indicator: 'macd' },
    })

    chart.chart.indicator = 'none'
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    expect(loadWorkspace().groups[0].windows.find(item => item.type === 'chart')).toMatchObject({
      chart: { indicator: 'none' },
    })
  })

  it('defaults missing volume visibility to shown and preserves a hidden pane', () => {
    const state = createDefaultWorkspace()
    const chart = state.groups[0].windows.find(item => item.type === 'chart')!
    if (chart.type !== 'chart') throw new Error('expected chart')
    delete (chart.chart as Partial<typeof chart.chart>).volumeVisible
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    expect(loadWorkspace().groups[0].windows.find(item => item.type === 'chart')).toMatchObject({
      chart: { volumeVisible: true },
    })

    chart.chart.volumeVisible = false
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    expect(loadWorkspace().groups[0].windows.find(item => item.type === 'chart')).toMatchObject({
      chart: { volumeVisible: false },
    })
  })

  it('falls back when persisted state is invalid', () => {
    window.localStorage.setItem(workspaceStorageKey, '{invalid')
    expect(loadWorkspace()).toEqual(createDefaultWorkspace())
  })

  it('normalizes missing focus and maximize references', () => {
    const state = createDefaultWorkspace()
    state.groups[0].focusedWindowId = 'missing'
    state.groups[0].maximizedWindowId = 'missing'
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))

    const loaded = loadWorkspace()
    expect(loaded.groups[0].focusedWindowId).toBe('list-primary')
    expect(loaded.groups[0].maximizedWindowId).toBeUndefined()
  })

  it('prefers valid v3 state over legacy state', () => {
    const state = createDefaultWorkspace()
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    window.localStorage.setItem(legacyWorkspaceStorageKey, JSON.stringify([{
      id: 'legacy',
      instrument: { symbol: 'legacy', name: 'legacy', kind: 'stock', exchange: 'SZ', rows: 1 },
      range: '1Y',
      priceMode: 'normal',
    }]))

    expect(loadWorkspace()).toEqual(state)
  })

  it('migrates v2 groups into validated split layouts', () => {
    window.localStorage.setItem(previousWorkspaceStorageKey, JSON.stringify({
      version: 2,
      activeGroupId: 'group-old',
      groups: [{
        id: 'group-old',
        name: '旧布局',
        layout: 'adaptive-grid',
        focusedWindowId: 'window-b',
        windows: [
          { id: 'window-a', instrument: instrument('000001.SZ'), chart: { range: '3Y', priceMode: 'normal' } },
          { id: 'window-b', instrument: instrument('510300.SH'), chart: { range: '10Y', priceMode: 'log' } },
        ],
      }],
    }))

    const migrated = loadWorkspace()
    expect(migrated).toMatchObject({
      version: 3,
      defaultGroupId: 'group-old',
      activeGroupId: 'group-old',
      groups: [{
        focusedWindowId: 'window-b',
        attachments: [],
        layout: { type: 'split', direction: 'horizontal' },
        windows: [{ type: 'chart', mode: 'detached' }, { type: 'chart', mode: 'detached' }],
      }],
    })
  })

  it('rebuilds an invalid v3 tree from the valid window order', () => {
    const state = createDefaultWorkspace()
    state.groups[0].windows.push({
      id: 'window-second',
      type: 'chart',
      title: '表3',
      mode: 'detached',
      presentation: { mode: 'docked' },
      instrument: instrument('510300.SH'),
      chart: { range: '1Y', priceMode: 'normal', volumeVisible: true, indicator: 'macd', settlementVisible: false, openInterestVisible: false, drawingToolbarCollapsed: false, tradingSystems: createTradingSystemWindowStates() },
    })
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))

    const recovered = loadWorkspace()
    expect(recovered.groups[0].layout).toMatchObject({ type: 'split', direction: 'horizontal' })
  })

  it('restores a corrupt group from its last known good snapshot', () => {
    const state = createDefaultWorkspace()
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))
    const persisted = JSON.parse(window.localStorage.getItem(workspaceStorageKey)!)
    persisted.groups[0].windows = []
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(persisted))

    const recovered = loadWorkspace()
    expect(recovered.groups[0].windows).toHaveLength(2)
    expect(recovered.groups[0].name).toBe('默认窗口组')
  })

  it('opens the configured default group instead of the last temporary group', () => {
    const state = createDefaultWorkspace()
    const second = createWindowGroup('临时查看组', 'four-charts', idFactory())
    state.groups.push(second)
    state.activeGroupId = second.id
    window.localStorage.setItem(workspaceStorageKey, JSON.stringify(state))

    expect(loadWorkspace().activeGroupId).toBe(state.defaultGroupId)
  })
})

describe('window group operations', () => {
  it('creates each supported group template with valid identities', () => {
    const ids = idFactory()
    const primary = createWindowGroup('自选布局', 'list-chart', ids)
    const comparison = createWindowGroup('对比布局', 'comparison', ids)
    const charts = createWindowGroup('四图布局', 'four-charts', ids)

    expect(primary).toMatchObject({ name: '自选布局', windows: [{ type: 'instrument-list' }, { type: 'chart', mode: 'attached' }] })
    expect(primary.attachments).toHaveLength(1)
    expect(comparison.windows).toHaveLength(3)
    expect(charts.windows).toHaveLength(4)
    expect(charts.attachments).toEqual([])
  })

  it('duplicates a group with remapped windows, layout, and attachments', () => {
    const source = createWindowGroup('源布局', 'comparison', idFactory())
    const sourceChart = source.windows.find(item => item.type === 'chart')!
    if (sourceChart.type !== 'chart') throw new Error('missing source chart')
    sourceChart.chart.tradingSystems.trend = {
      ...sourceChart.chart.tradingSystems.trend,
      enabled: true,
      expanded: false,
      isolate: true,
      layers: { ...sourceChart.chart.tradingSystems.trend.layers, patterns: false },
      settings: { ...sourceChart.chart.tradingSystems.trend.settings, shortHorizonBars: 80 },
    }
    const copied = duplicateWindowGroup(source, '源布局 副本', idFactory())

    expect(copied.name).toBe('源布局 副本')
    expect(copied.windows.map(item => item.id)).not.toEqual(source.windows.map(item => item.id))
    expect(copied.attachments[0].sourceWindowId).toBe(copied.windows[0].id)
    expect(copied.attachments[0].targetWindowId).toBe(copied.windows[1].id)
    expect(JSON.stringify(copied.layout)).not.toContain(source.windows[0].id)
    const copiedChart = copied.windows.find(item => item.type === 'chart')!
    if (copiedChart.type !== 'chart') throw new Error('missing copied chart')
    expect(copiedChart.chart.tradingSystems).toEqual(sourceChart.chart.tradingSystems)
    expect(copiedChart.chart.tradingSystems).not.toBe(sourceChart.chart.tradingSystems)
    copiedChart.chart.tradingSystems.trend.layers.patterns = true
    expect(sourceChart.chart.tradingSystems.trend.layers.patterns).toBe(false)
  })
})

function instrument(symbol: string) {
  return { symbol, name: symbol, kind: 'stock', exchange: 'SZ', rows: 1 }
}

function idFactory() {
  let next = 0
  return (prefix: string) => `${prefix}-${++next}`
}
