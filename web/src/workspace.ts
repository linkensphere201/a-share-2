import type { ChartRange, PriceMode, VisibleRange } from './ChartCanvas'

export type Instrument = {
  symbol: string
  name: string
  kind: string
  exchange: string
  category?: string
  first_trade_date?: string
  last_trade_date?: string
  rows: number
}

export type ChartViewState = {
  range: ChartRange
  priceMode: PriceMode
  visibleRange?: VisibleRange
}

export type InstrumentWindowState = {
  id: string
  instrument: Instrument
  chart: ChartViewState
}

export type WindowGroupState = {
  id: string
  name: string
  layout: 'adaptive-grid'
  windows: InstrumentWindowState[]
  focusedWindowId: string
  maximizedWindowId?: string
}

export type WorkspaceState = {
  version: 2
  activeGroupId: string
  groups: WindowGroupState[]
}

type LegacyCanvasState = {
  id: string
  instrument: Instrument
  range: ChartRange
  priceMode: PriceMode
  visibleRange?: VisibleRange
}

export const workspaceStorageKey = 'stock-harness.workspace.v2'
export const legacyWorkspaceStorageKey = 'stock-harness.workspace.v1'
export const chartRanges: ChartRange[] = ['1Y', '3Y', '10Y', 'ALL']

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

export function createDefaultWorkspace(): WorkspaceState {
  const initialWindow: InstrumentWindowState = {
    id: 'window-primary',
    instrument: fallbackInstrument,
    chart: { range: '3Y', priceMode: 'normal' },
  }
  return {
    version: 2,
    activeGroupId: 'group-primary',
    groups: [{
      id: 'group-primary',
      name: '默认窗口组',
      layout: 'adaptive-grid',
      windows: [initialWindow],
      focusedWindowId: initialWindow.id,
    }],
  }
}

export function loadWorkspace(storage: Pick<Storage, 'getItem'> = window.localStorage): WorkspaceState {
  const current = parseJson(storage.getItem(workspaceStorageKey))
  const normalized = normalizeWorkspace(current)
  if (normalized) return normalized

  const legacy = parseJson(storage.getItem(legacyWorkspaceStorageKey))
  const windows = Array.isArray(legacy) ? legacy.map(migrateLegacyWindow).filter(isDefined).slice(0, 4) : []
  if (windows.length === 0) return createDefaultWorkspace()

  return {
    version: 2,
    activeGroupId: 'group-primary',
    groups: [{
      id: 'group-primary',
      name: '默认窗口组',
      layout: 'adaptive-grid',
      windows,
      focusedWindowId: windows[0].id,
    }],
  }
}

export function saveWorkspace(state: WorkspaceState, storage: Pick<Storage, 'setItem'> = window.localStorage) {
  storage.setItem(workspaceStorageKey, JSON.stringify(state))
}

function normalizeWorkspace(value: unknown): WorkspaceState | undefined {
  if (!isRecord(value) || value.version !== 2 || !Array.isArray(value.groups)) return undefined
  const groups = value.groups.map(normalizeGroup).filter(isDefined)
  if (groups.length === 0) return undefined
  const activeGroupId = groups.some(group => group.id === value.activeGroupId)
    ? String(value.activeGroupId)
    : groups[0].id
  return { version: 2, activeGroupId, groups }
}

function normalizeGroup(value: unknown): WindowGroupState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.windows)) return undefined
  const windows = value.windows.map(normalizeWindow).filter(isDefined).slice(0, 4)
  if (windows.length === 0) return undefined
  const focusedWindowId = windows.some(item => item.id === value.focusedWindowId)
    ? String(value.focusedWindowId)
    : windows[0].id
  const maximizedWindowId = windows.some(item => item.id === value.maximizedWindowId)
    ? String(value.maximizedWindowId)
    : undefined
  return {
    id: value.id,
    name: typeof value.name === 'string' ? value.name : '窗口组',
    layout: 'adaptive-grid',
    windows,
    focusedWindowId,
    maximizedWindowId,
  }
}

function normalizeWindow(value: unknown): InstrumentWindowState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !isInstrument(value.instrument) || !isRecord(value.chart)) return undefined
  if (!isChartRange(value.chart.range) || !isPriceMode(value.chart.priceMode)) return undefined
  return {
    id: value.id,
    instrument: value.instrument,
    chart: {
      range: value.chart.range,
      priceMode: value.chart.priceMode,
      visibleRange: normalizeVisibleRange(value.chart.visibleRange),
    },
  }
}

function migrateLegacyWindow(value: unknown): InstrumentWindowState | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || !isInstrument(value.instrument)) return undefined
  if (!isChartRange(value.range) || !isPriceMode(value.priceMode)) return undefined
  const legacy = value as LegacyCanvasState
  return {
    id: legacy.id,
    instrument: legacy.instrument,
    chart: {
      range: legacy.range,
      priceMode: legacy.priceMode,
      visibleRange: normalizeVisibleRange(legacy.visibleRange),
    },
  }
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
