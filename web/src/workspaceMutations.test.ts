import { describe, expect, it } from 'vitest'
import { createWindowGroup, type Instrument, type WorkspaceState } from './workspace'
import {
  applyListSelection,
  removeMissingCustomGroupReferences,
  removeInstrumentFromManualList,
  removeWorkspaceWindow,
  replaceDetachedWindowInstruments,
  resolveActiveChart,
  samePaneRatios,
} from './workspaceMutations'

function fixture() {
  let sequence = 0
  return createWindowGroup('测试', 'list-chart', prefix => `${prefix}-${++sequence}`)
}

const replacement: Instrument = {
  symbol: '000001.SZ',
  name: '平安银行',
  kind: 'stock',
  exchange: 'SZ',
  rows: 100,
}

describe('workspace mutations', () => {
  it('routes a list selection to its attached chart', () => {
    const group = fixture()
    const list = group.windows.find(item => item.type === 'instrument-list')!
    const updated = applyListSelection(group, list.id, replacement)
    const chart = updated.windows.find(item => item.type === 'chart')!

    expect(updated.windows.find(item => item.id === list.id)).toMatchObject({
      selectedSymbol: replacement.symbol,
    })
    expect(chart.instrument).toEqual(replacement)
    expect(resolveActiveChart(updated, updated.windows[0])).toBe(chart)
  })

  it('replaces a detached list and preserves a valid selection', () => {
    const group = fixture()
    const list = group.windows.find(item => item.type === 'instrument-list')!
    const updated = replaceDetachedWindowInstruments(group, list.id, [replacement])

    expect(updated.windows.find(item => item.id === list.id)).toMatchObject({
      selectedSymbol: replacement.symbol,
      content: { instruments: [replacement] },
    })
  })

  it('removes a fixed-list instrument and routes the adjacent selection', () => {
    const group = fixture()
    const list = group.windows.find(item => item.type === 'instrument-list')!
    if (list.type !== 'instrument-list') throw new Error('list fixture expected')
    const adjacent = { ...replacement, symbol: '000002.SZ', name: '万科A' }
    list.content.instruments = [replacement, adjacent]
    list.selectedSymbol = replacement.symbol

    const result = removeInstrumentFromManualList(group, list.id, replacement.symbol)
    const updatedList = result.group.windows.find(item => item.id === list.id)
    const chart = result.group.windows.find(item => item.type === 'chart')

    expect(result.removed).toBe(true)
    expect(updatedList).toMatchObject({
      selectedSymbol: adjacent.symbol,
      content: { instruments: [adjacent] },
    })
    expect(chart).toMatchObject({ instrument: adjacent })
  })

  it('removes layout and attachment references with a window', () => {
    const group = fixture()
    const chart = group.windows.find(item => item.type === 'chart')!
    const updated = removeWorkspaceWindow(group, chart.id)

    expect(updated.windows).toHaveLength(1)
    expect(updated.attachments).toEqual([])
    expect(updated.focusedWindowId).toBe(updated.windows[0].id)
  })

  it('compares every persisted pane ratio', () => {
    const ratios = { price: 3, volume: 1, macd: 1, openInterest: 1 }
    expect(samePaneRatios(ratios, { ...ratios })).toBe(true)
    expect(samePaneRatios(ratios, { ...ratios, macd: 2 })).toBe(false)
  })

  it('removes only missing custom-group references from every saved group', () => {
    const group = fixture()
    const list = group.windows.find(item => item.type === 'instrument-list')!
    const existingGroup = { ...replacement, symbol: 'CUSTOM:keep', kind: 'custom-group' }
    const missingGroup = { ...replacement, symbol: 'CUSTOM:remove', kind: 'custom-group' }
    const stock = { ...replacement }
    if (list.type !== 'instrument-list') throw new Error('list fixture expected')
    list.content.instruments = [missingGroup, existingGroup, stock]
    list.selectedSymbol = missingGroup.symbol
    const workspace: WorkspaceState = {
      version: 3,
      defaultGroupId: group.id,
      activeGroupId: group.id,
      groups: [group],
      recoveryGroups: { [group.id]: structuredClone(group) },
    }

    const updated = removeMissingCustomGroupReferences(
      workspace,
      new Set([existingGroup.symbol]),
    )
    const updatedList = updated.groups[0].windows.find(item => item.id === list.id)

    expect(updatedList).toMatchObject({
      selectedSymbol: existingGroup.symbol,
      content: { instruments: [existingGroup, stock] },
    })
    expect(workspace.groups[0].windows.find(item => item.id === list.id)).toMatchObject({
      content: { instruments: [missingGroup, existingGroup, stock] },
    })
  })
})
