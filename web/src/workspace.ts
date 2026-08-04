import type { ChartIndicator, ChartRange, PriceMode, VisibleRange } from './ChartCanvas'
import {
  createLayoutTree,
  parseLayoutTree,
  updateSplitRatio,
  validateLayoutTree,
  type WindowLayoutNode,
} from './layoutTree'
import {
  validateWindowAttachments,
  type WindowAttachment,
} from './windowAttachments'

export type Instrument = {
  symbol: string
  name: string
  kind: string
  exchange: string
  category?: string
  source_system?: string
  family?: string
  classification?: string
  classification_label?: string
  source_label?: string
  first_trade_date?: string
  last_trade_date?: string
  rows: number
}

export type ChartViewState = {
  range: ChartRange
  priceMode: PriceMode
  volumeVisible: boolean
  indicator: ChartIndicator
  visibleRange?: VisibleRange
}

export type ChartWindowState = {
  id: string
  type: 'chart'
  title: string
  mode: 'attached' | 'detached'
  instrument: Instrument
  chart: ChartViewState
}

export type InstrumentListWindowState = {
  id: string
  type: 'instrument-list'
  title: string
  mode: 'attached' | 'detached'
  content: {
    mode: 'manual'
    instruments: Instrument[]
  }
  selectedSymbol?: string
  memberSourceWindowId?: string
  sort?: {
    key: 'name' | 'total_market_cap' | 'change_percent'
    direction: 'asc' | 'desc'
  }
}

export type WorkspaceWindowState = ChartWindowState | InstrumentListWindowState
export type InstrumentWindowState = ChartWindowState

export type WindowGroupState = {
  id: string
  name: string
  layout: WindowLayoutNode
  windows: WorkspaceWindowState[]
  attachments: WindowAttachment[]
  focusedWindowId: string
  maximizedWindowId?: string
}

export type WorkspaceState = {
  version: 3
  defaultGroupId: string
  activeGroupId: string
  groups: WindowGroupState[]
  recoveryGroups: Record<string, WindowGroupState>
}

type LegacyCanvasState = {
  id: string
  instrument: Instrument
  range: ChartRange
  priceMode: PriceMode
  visibleRange?: VisibleRange
}

export const workspaceStorageKey = 'stock-harness.workspace.v3'
export const previousWorkspaceStorageKey = 'stock-harness.workspace.v2'
export const legacyWorkspaceStorageKey = 'stock-harness.workspace.v1'
export const chartRanges: ChartRange[] = ['1M', '1Y', '3Y', '10Y', 'ALL']
export type WindowGroupTemplate = 'list-chart' | 'comparison' | 'four-charts'
export type WorkspaceIdFactory = (prefix: string) => string

const fallbackInstrument: Instrument = {
  symbol: 'BK1128.DC',
  name: 'CPO概念',
  kind: 'sector',
  exchange: 'DC',
  category: '概念板块',
  rows: 843,
  first_trade_date: '2023-02-10',
  last_trade_date: '2026-07-31',
}

export function createWindowGroup(
  name: string,
  template: WindowGroupTemplate = 'list-chart',
  createId: WorkspaceIdFactory = createWorkspaceId,
): WindowGroupState {
  const groupId = createId('group')
  let windowNumber = 0
  const nextTitle = () => `表${++windowNumber}`
  const chart = (mode: ChartWindowState['mode']): ChartWindowState => ({
    id: createId('chart'),
    type: 'chart',
    title: nextTitle(),
    mode,
    instrument: { ...fallbackInstrument },
    chart: { range: '3Y', priceMode: 'normal', volumeVisible: true, indicator: 'macd' },
  })
  const list = (): InstrumentListWindowState => ({
    id: createId('list'),
    type: 'instrument-list',
    title: nextTitle(),
    mode: 'detached',
    content: { mode: 'manual', instruments: [{ ...fallbackInstrument }] },
    selectedSymbol: fallbackInstrument.symbol,
  })

  if (template === 'four-charts') {
    const windows = [chart('detached'), chart('detached'), chart('detached'), chart('detached')]
    return createGroup(groupId, name, windows, windows[0].id)
  }

  const listWindow = list()
  const attachedChart = chart('attached')
  const windows: WorkspaceWindowState[] = template === 'comparison'
    ? [listWindow, attachedChart, chart('detached')]
    : [listWindow, attachedChart]
  return createGroup(groupId, name, windows, attachedChart.id, [{
    id: createId('attachment'),
    type: 'show-symbol',
    sourceWindowId: listWindow.id,
    targetWindowId: attachedChart.id,
  }])
}

export function duplicateWindowGroup(
  source: WindowGroupState,
  name: string,
  createId: WorkspaceIdFactory = createWorkspaceId,
): WindowGroupState {
  const windowIds = new Map(source.windows.map(window => [window.id, createId(window.type === 'chart' ? 'chart' : 'list')]))
  const windows = source.windows.map(window => {
    const copied = { ...structuredClone(window), id: windowIds.get(window.id)! }
    return copied.type === 'instrument-list' && copied.memberSourceWindowId
      ? { ...copied, memberSourceWindowId: windowIds.get(copied.memberSourceWindowId) }
      : copied
  })
  return {
    id: createId('group'),
    name,
    layout: remapLayout(source.layout, windowIds, createId),
    windows,
    attachments: source.attachments.map(edge => ({
      ...edge,
      id: createId('attachment'),
      sourceWindowId: windowIds.get(edge.sourceWindowId)!,
      targetWindowId: windowIds.get(edge.targetWindowId)!,
    })),
    focusedWindowId: windowIds.get(source.focusedWindowId) ?? windows[0].id,
  }
}

export function createDefaultWorkspace(): WorkspaceState {
  const listWindow: InstrumentListWindowState = {
    id: 'list-primary',
    type: 'instrument-list',
    title: '表1',
    mode: 'detached',
    content: { mode: 'manual', instruments: [fallbackInstrument] },
    selectedSymbol: fallbackInstrument.symbol,
  }
  const chartWindow: ChartWindowState = {
    id: 'chart-primary',
    type: 'chart',
    title: '表2',
    mode: 'attached',
    instrument: fallbackInstrument,
    chart: { range: '3Y', priceMode: 'normal', volumeVisible: true, indicator: 'macd' },
  }
  const group = createGroup('group-primary', '默认窗口组', [listWindow, chartWindow], chartWindow.id, [{
    id: 'attachment-primary',
    type: 'show-symbol',
    sourceWindowId: listWindow.id,
    targetWindowId: chartWindow.id,
  }])
  if (group.layout.type === 'split') group.layout = updateSplitRatio(group.layout, group.layout.id, 0.25)
  return { version: 3, defaultGroupId: group.id, activeGroupId: group.id, groups: [group], recoveryGroups: { [group.id]: structuredClone(group) } }
}

export function loadWorkspace(storage: Pick<Storage, 'getItem'> = window.localStorage): WorkspaceState {
  const current = normalizeWorkspace(parseJson(storage.getItem(workspaceStorageKey)))
  if (current) return current
  const previous = migrateV2Workspace(parseJson(storage.getItem(previousWorkspaceStorageKey)))
  if (previous) return previous

  const legacy = parseJson(storage.getItem(legacyWorkspaceStorageKey))
  const windows = Array.isArray(legacy) ? legacy.map(migrateLegacyWindow).filter(isDefined).slice(0, 4) : []
  if (windows.length === 0) return createDefaultWorkspace()
  const group = createGroup('group-primary', '默认窗口组', windows, windows[0].id)
  return { version: 3, defaultGroupId: group.id, activeGroupId: group.id, groups: [group], recoveryGroups: { [group.id]: structuredClone(group) } }
}

export function saveWorkspace(state: WorkspaceState, storage: Pick<Storage, 'setItem'> = window.localStorage) {
  const recoveryGroups = Object.fromEntries(state.groups.map(group => [group.id, structuredClone(group)]))
  storage.setItem(workspaceStorageKey, JSON.stringify({ ...state, recoveryGroups }))
}

export function deriveReferencedSymbols(
  group: WindowGroupState,
  resolvedByWindow: Record<string, string[]> = {},
): string[] {
  const symbols = new Set<string>()
  group.windows.forEach(window => {
    if (window.type === 'chart') {
      symbols.add(window.instrument.symbol)
      return
    }
    window.content.instruments.forEach(instrument => symbols.add(instrument.symbol))
    resolvedByWindow[window.id]?.forEach(symbol => symbols.add(symbol))
  })
  return [...symbols].sort()
}

function normalizeWorkspace(value: unknown): WorkspaceState | undefined {
  if (!isRecord(value) || value.version !== 3 || !Array.isArray(value.groups)) return undefined
  const recovered = isRecord(value.recoveryGroups)
    ? Object.fromEntries(Object.entries(value.recoveryGroups).map(([id, group]) => [id, normalizeGroup(group)]).filter((entry): entry is [string, WindowGroupState] => entry[1] !== undefined))
    : {}
  const groups = value.groups.map(item => normalizeGroup(item) ?? recoverGroup(item, recovered)).filter(isDefined)
  if (groups.length === 0) return undefined
  const defaultGroupId = groupIdOrFallback(groups, value.defaultGroupId)
  const activeGroupId = defaultGroupId
  const recoveryGroups = Object.fromEntries(groups.map(group => [group.id, recovered[group.id] ?? structuredClone(group)]))
  return { version: 3, defaultGroupId, activeGroupId, groups, recoveryGroups }
}

function normalizeGroup(value: unknown): WindowGroupState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.windows)) return undefined
  let windows = value.windows.map(normalizeWindow).filter(isDefined).slice(0, 8)
  if (windows.length === 0) return undefined
  const windowIds = windows.map(item => item.id)
  const parsedLayout = parseLayoutTree(value.layout)
  const layout = parsedLayout && validateLayoutTree(parsedLayout, windowIds).length === 0
    ? parsedLayout
    : createLayoutTree(windowIds, `layout-${value.id}`)
  const parsedAttachments = Array.isArray(value.attachments)
    ? value.attachments.map(normalizeAttachment).filter(isDefined)
    : []
  windows = windows.map(window => window.type === 'instrument-list' && parsedAttachments.some(
    edge => edge.type === 'show-members' && edge.targetWindowId === window.id
  ) ? { ...window, mode: 'attached' } : window)
  const attachments = validateWindowAttachments(parsedAttachments, windows).length === 0 ? parsedAttachments : []
  return {
    id: value.id,
    name: typeof value.name === 'string' ? value.name : '窗口组',
    layout,
    windows,
    attachments,
    focusedWindowId: windowIds.includes(String(value.focusedWindowId)) ? String(value.focusedWindowId) : windowIds[0],
    maximizedWindowId: windowIds.includes(String(value.maximizedWindowId)) ? String(value.maximizedWindowId) : undefined,
  }
}

function migrateV2Workspace(value: unknown): WorkspaceState | undefined {
  if (!isRecord(value) || value.version !== 2 || !Array.isArray(value.groups)) return undefined
  const groups = value.groups.map(migrateV2Group).filter(isDefined)
  if (groups.length === 0) return undefined
  const activeGroupId = groupIdOrFallback(groups, value.activeGroupId)
  return { version: 3, defaultGroupId: activeGroupId, activeGroupId, groups, recoveryGroups: Object.fromEntries(groups.map(group => [group.id, structuredClone(group)])) }
}

function migrateV2Group(value: unknown): WindowGroupState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.windows)) return undefined
  const windows = value.windows.map(normalizeChartWindow).filter(isDefined).slice(0, 4)
  if (windows.length === 0) return undefined
  return createGroup(
    value.id,
    typeof value.name === 'string' ? value.name : '窗口组',
    windows,
    typeof value.focusedWindowId === 'string' ? value.focusedWindowId : windows[0].id,
    [],
    typeof value.maximizedWindowId === 'string' ? value.maximizedWindowId : undefined,
  )
}

function createGroup(
  id: string,
  name: string,
  windows: WorkspaceWindowState[],
  focusedWindowId: string,
  attachments: WindowAttachment[] = [],
  maximizedWindowId?: string,
): WindowGroupState {
  const windowIds = windows.map(item => item.id)
  return {
    id,
    name,
    layout: createLayoutTree(windowIds, `layout-${id}`),
    windows,
    attachments,
    focusedWindowId: windowIds.includes(focusedWindowId) ? focusedWindowId : windowIds[0],
    maximizedWindowId: maximizedWindowId && windowIds.includes(maximizedWindowId) ? maximizedWindowId : undefined,
  }
}

function remapLayout(
  node: WindowLayoutNode,
  windowIds: Map<string, string>,
  createId: WorkspaceIdFactory,
): WindowLayoutNode {
  if (node.type === 'window') {
    return { type: 'window', id: createId('layout'), windowId: windowIds.get(node.windowId)! }
  }
  return {
    type: 'split',
    id: createId('split'),
    direction: node.direction,
    ratio: node.ratio,
    first: remapLayout(node.first, windowIds, createId),
    second: remapLayout(node.second, windowIds, createId),
  }
}

function createWorkspaceId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`
}

function normalizeWindow(value: unknown): WorkspaceWindowState | undefined {
  if (!isRecord(value)) return undefined
  if (value.type === 'instrument-list') return normalizeListWindow(value)
  return normalizeChartWindow(value)
}

function normalizeChartWindow(value: unknown): ChartWindowState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !isInstrument(value.instrument) || !isRecord(value.chart)) return undefined
  if (!isChartRange(value.chart.range) || !isPriceMode(value.chart.priceMode)) return undefined
  return {
    id: value.id,
    type: 'chart',
    title: typeof value.title === 'string' ? value.title : value.instrument.name,
    mode: value.mode === 'attached' ? 'attached' : 'detached',
    instrument: value.instrument,
    chart: {
      range: value.chart.range,
      priceMode: value.chart.priceMode,
      volumeVisible: value.chart.volumeVisible !== false,
      indicator: value.chart.indicator === 'none' ? 'none' : 'macd',
      visibleRange: normalizeVisibleRange(value.chart.visibleRange),
    },
  }
}

function normalizeListWindow(value: Record<string, unknown>): InstrumentListWindowState | undefined {
  if (typeof value.id !== 'string' || !isRecord(value.content) || value.content.mode !== 'manual') return undefined
  const instruments = Array.isArray(value.content.instruments)
    ? value.content.instruments.filter(isInstrument).filter(uniqueInstrument)
    : []
  const selectedSymbol = instruments.some(item => item.symbol === value.selectedSymbol)
    ? String(value.selectedSymbol)
    : undefined
  return {
    id: value.id,
    type: 'instrument-list',
    title: typeof value.title === 'string' ? value.title : '标的列表',
    mode: value.mode === 'attached' ? 'attached' : 'detached',
    content: { mode: 'manual', instruments },
    selectedSymbol,
    memberSourceWindowId: typeof value.memberSourceWindowId === 'string'
      ? value.memberSourceWindowId
      : undefined,
    sort: normalizeListSort(value.sort),
  }
}

function normalizeListSort(value: unknown): InstrumentListWindowState['sort'] {
  if (!isRecord(value)) return undefined
  if (!['name', 'total_market_cap', 'change_percent'].includes(String(value.key))) return undefined
  if (value.direction !== 'asc' && value.direction !== 'desc') return undefined
  return {
    key: value.key as NonNullable<InstrumentListWindowState['sort']>['key'],
    direction: value.direction,
  }
}

function normalizeAttachment(value: unknown): WindowAttachment | undefined {
  if (!isRecord(value) || typeof value.id !== 'string') return undefined
  if (value.type !== 'show-symbol' && value.type !== 'show-members') return undefined
  if (typeof value.sourceWindowId !== 'string' || typeof value.targetWindowId !== 'string') return undefined
  return { id: value.id, type: value.type, sourceWindowId: value.sourceWindowId, targetWindowId: value.targetWindowId }
}

function migrateLegacyWindow(value: unknown): ChartWindowState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !isInstrument(value.instrument)) return undefined
  if (!isChartRange(value.range) || !isPriceMode(value.priceMode)) return undefined
  const legacy = value as LegacyCanvasState
  return {
    id: legacy.id,
    type: 'chart',
    title: legacy.instrument.name,
    mode: 'detached',
    instrument: legacy.instrument,
    chart: { range: legacy.range, priceMode: legacy.priceMode, volumeVisible: true, indicator: 'macd', visibleRange: normalizeVisibleRange(legacy.visibleRange) },
  }
}

function groupIdOrFallback(groups: WindowGroupState[], value: unknown): string {
  return groups.some(group => group.id === value) ? String(value) : groups[0].id
}

function recoverGroup(value: unknown, recovered: Record<string, WindowGroupState>): WindowGroupState | undefined {
  return isRecord(value) && typeof value.id === 'string' ? recovered[value.id] : undefined
}

function normalizeVisibleRange(value: unknown): VisibleRange | undefined {
  return isRecord(value) && typeof value.from === 'string' && typeof value.to === 'string'
    ? { from: value.from, to: value.to }
    : undefined
}

function isInstrument(value: unknown): value is Instrument {
  return isRecord(value)
    && typeof value.symbol === 'string'
    && typeof value.name === 'string'
    && typeof value.kind === 'string'
    && typeof value.exchange === 'string'
    && typeof value.rows === 'number'
}

function uniqueInstrument(value: Instrument, index: number, values: Instrument[]): boolean {
  return values.findIndex(item => item.symbol === value.symbol) === index
}

function isChartRange(value: unknown): value is ChartRange {
  return chartRanges.includes(value as ChartRange)
}

function isPriceMode(value: unknown): value is PriceMode {
  return value === 'normal' || value === 'log'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isDefined<T>(value: T | undefined): value is T {
  return value !== undefined
}

function parseJson(value: string | null): unknown {
  if (!value) return undefined
  try {
    return JSON.parse(value)
  } catch {
    return undefined
  }
}
