import { removeLayoutWindow } from './layoutTree'
import { removeWindowAttachments } from './windowAttachments'
import {
  isChartableInstrument,
  isListableInstrument,
  type ChartPaneRatios,
  type ChartWindowState,
  type Instrument,
  type InstrumentListWindowState,
  type WindowGroupState,
  type WorkspaceState,
  type WorkspaceWindowState,
} from './workspace'

export function resolveActiveChart(
  group: WindowGroupState,
  focused: WorkspaceWindowState,
): ChartWindowState | undefined {
  if (focused.type === 'chart') return focused
  const targetId = group.attachments.find(edge => (
    edge.sourceWindowId === focused.id && edge.type === 'show-symbol'
  ))?.targetWindowId
  const target = group.windows.find(item => item.id === targetId)
  if (target?.type === 'chart') return target
  return group.windows.find((item): item is ChartWindowState => item.type === 'chart')
}

export function removeWorkspaceWindow(
  group: WindowGroupState,
  windowId: string,
): WindowGroupState {
  if (group.windows.length === 1) return group
  const index = group.windows.findIndex(item => item.id === windowId)
  if (index < 0) return group
  const windows = group.windows.filter(item => item.id !== windowId)
  const layout = removeLayoutWindow(group.layout, windowId)
  if (!layout) return group
  return {
    ...group,
    layout,
    windows,
    attachments: removeWindowAttachments(group.attachments, windowId),
    focusedWindowId: group.focusedWindowId === windowId
      ? windows[Math.min(index, windows.length - 1)].id
      : group.focusedWindowId,
    maximizedWindowId: group.maximizedWindowId === windowId
      ? undefined
      : group.maximizedWindowId,
  }
}

export function applyListSelection(
  group: WindowGroupState,
  sourceId: string,
  instrument: Instrument,
): WindowGroupState {
  const edges = group.attachments.filter(
    attachment => attachment.sourceWindowId === sourceId,
  )
  return {
    ...group,
    windows: group.windows.map(item => {
      if (item.id === sourceId && item.type === 'instrument-list') {
        return {
          ...item,
          selectedSymbol: instrument.symbol,
        } satisfies InstrumentListWindowState
      }
      const symbolEdge = edges.find(edge => (
        edge.targetWindowId === item.id && edge.type === 'show-symbol'
      ))
      if (
        symbolEdge
        && item.type === 'chart'
        && item.mode === 'attached'
        && isChartableInstrument(instrument)
      ) {
        return {
          ...item,
          instrument,
          chart: { ...item.chart, visibleRange: undefined },
        }
      }
      const membersEdge = edges.find(edge => (
        edge.targetWindowId === item.id && edge.type === 'show-members'
      ))
      if (membersEdge && item.type === 'instrument-list' && item.mode === 'attached') {
        return {
          ...item,
          memberSourceWindowId: sourceId,
          selectedSymbol: undefined,
        }
      }
      return item
    }),
  }
}

export function replaceDetachedWindowInstruments(
  group: WindowGroupState,
  windowId: string,
  instruments: Instrument[],
): WindowGroupState {
  const source = group.windows.find(item => item.id === windowId)
  if (!source || source.mode !== 'detached') return group
  const selectable = instruments.filter(isListableInstrument)
  if (source.type === 'chart') {
    const instrument = selectable[0]
    if (!instrument || !isChartableInstrument(instrument)) return group
    return {
      ...group,
      windows: group.windows.map(item => item.id === windowId
        ? {
          ...source,
          instrument,
          chart: { ...source.chart, visibleRange: undefined },
        }
        : item),
    }
  }
  const nextSelection = selectable.find(
    item => item.symbol === source.selectedSymbol,
  ) ?? selectable[0]
  const updated = {
    ...group,
    windows: group.windows.map(item => item.id === windowId
      ? {
        ...source,
        content: { ...source.content, instruments: selectable },
        selectedSymbol: nextSelection?.symbol,
      }
      : item),
  }
  return nextSelection
    ? applyListSelection(updated, windowId, nextSelection)
    : updated
}

export function removeMissingCustomGroupReferences(
  workspace: WorkspaceState,
  existingSymbols: ReadonlySet<string>,
): WorkspaceState {
  let changed = false
  const groups = workspace.groups.map(group => {
    let groupChanged = false
    const windows = group.windows.map(window => {
      if (window.type !== 'instrument-list') return window
      const instruments = window.content.instruments.filter(instrument => (
        instrument.kind !== 'custom-group' || existingSymbols.has(instrument.symbol)
      ))
      if (instruments.length === window.content.instruments.length) return window
      groupChanged = true
      const selectedSymbol = instruments.some(item => item.symbol === window.selectedSymbol)
        ? window.selectedSymbol
        : instruments[0]?.symbol
      return {
        ...window,
        content: { ...window.content, instruments },
        selectedSymbol,
      }
    })
    if (!groupChanged) return group
    changed = true
    return { ...group, windows }
  })
  return changed ? { ...workspace, groups } : workspace
}

export function samePaneRatios(
  left: ChartPaneRatios | undefined,
  right: ChartPaneRatios,
): boolean {
  return left?.price === right.price
    && left.volume === right.volume
    && left.macd === right.macd
    && left.openInterest === right.openInterest
}
