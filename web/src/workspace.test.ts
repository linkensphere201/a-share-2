// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import {
  createDefaultWorkspace,
  createWindowGroup,
  chartRanges,
  duplicateWindowGroup,
  deriveReferencedSymbols,
  legacyWorkspaceStorageKey,
  loadWorkspace,
  previousWorkspaceStorageKey,
  workspaceStorageKey,
} from './workspace'

afterEach(() => window.localStorage.clear())

describe('workspace persistence', () => {
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
      instrument: instrument('510300.SH'),
      chart: { range: '1Y', priceMode: 'normal' },
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
    const copied = duplicateWindowGroup(source, '源布局 副本', idFactory())

    expect(copied.name).toBe('源布局 副本')
    expect(copied.windows.map(item => item.id)).not.toEqual(source.windows.map(item => item.id))
    expect(copied.attachments[0].sourceWindowId).toBe(copied.windows[0].id)
    expect(copied.attachments[0].targetWindowId).toBe(copied.windows[1].id)
    expect(JSON.stringify(copied.layout)).not.toContain(source.windows[0].id)
  })
})

function instrument(symbol: string) {
  return { symbol, name: symbol, kind: 'stock', exchange: 'SZ', rows: 1 }
}

function idFactory() {
  let next = 0
  return (prefix: string) => `${prefix}-${++next}`
}
