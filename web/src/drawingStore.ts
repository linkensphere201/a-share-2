import type { PriceMode } from './ChartCanvas'

export type TrendLineSnap = 'free' | 'high' | 'low'

export type TrendLineAnchor = {
  date: string
  price: number
  snap: TrendLineSnap
}

export type TrendLineStyle = {
  color: string
  width: 1 | 2 | 3
  dash: 'solid' | 'dashed'
}

export type TrendLineDrawing = {
  id: string
  kind: 'trend-line'
  symbol: string
  anchors: [TrendLineAnchor, TrendLineAnchor]
  coordinateMode: PriceMode
  style: TrendLineStyle
  createdAt: string
  updatedAt: string
}

type DrawingStoreState = {
  version: 1
  symbols: Record<string, TrendLineDrawing[]>
}

const drawingStorageKey = 'stock-harness.drawings.v1'
const drawingChangeEvent = 'stock-harness:drawings-changed'

export const defaultTrendLineStyle: TrendLineStyle = {
  color: '#f0b85a',
  width: 2,
  dash: 'solid',
}

export function loadSymbolDrawings(symbol: string, storage: Storage = window.localStorage): TrendLineDrawing[] {
  return readState(storage).symbols[symbol]?.map(cloneDrawing) ?? []
}

export function saveTrendLine(drawing: TrendLineDrawing, storage: Storage = window.localStorage): void {
  const state = readState(storage)
  const current = state.symbols[drawing.symbol] ?? []
  const index = current.findIndex(item => item.id === drawing.id)
  const next = current.map(cloneDrawing)
  if (index >= 0) next[index] = cloneDrawing(drawing)
  else next.push(cloneDrawing(drawing))
  state.symbols[drawing.symbol] = next
  writeState(state, storage)
  notify(drawing.symbol)
}

export function deleteTrendLine(symbol: string, id: string, storage: Storage = window.localStorage): void {
  const state = readState(storage)
  const next = (state.symbols[symbol] ?? []).filter(item => item.id !== id)
  if (next.length === (state.symbols[symbol] ?? []).length) return
  if (next.length > 0) state.symbols[symbol] = next
  else delete state.symbols[symbol]
  writeState(state, storage)
  notify(symbol)
}

export function subscribeSymbolDrawings(symbol: string, listener: () => void): () => void {
  const onCustomChange = (event: Event) => {
    if ((event as CustomEvent<{ symbol?: string }>).detail?.symbol === symbol) listener()
  }
  const onStorageChange = (event: StorageEvent) => {
    if (event.key === drawingStorageKey) listener()
  }
  window.addEventListener(drawingChangeEvent, onCustomChange)
  window.addEventListener('storage', onStorageChange)
  return () => {
    window.removeEventListener(drawingChangeEvent, onCustomChange)
    window.removeEventListener('storage', onStorageChange)
  }
}

export function createTrendLine(
  symbol: string,
  anchors: [TrendLineAnchor, TrendLineAnchor],
  coordinateMode: PriceMode,
  now = new Date(),
  createId: () => string = () => crypto.randomUUID(),
): TrendLineDrawing {
  const timestamp = now.toISOString()
  return {
    id: createId(),
    kind: 'trend-line',
    symbol,
    anchors,
    coordinateMode,
    style: { ...defaultTrendLineStyle },
    createdAt: timestamp,
    updatedAt: timestamp,
  }
}

function readState(storage: Storage): DrawingStoreState {
  try {
    const parsed = JSON.parse(storage.getItem(drawingStorageKey) ?? '') as unknown
    if (!isObject(parsed) || parsed.version !== 1 || !isObject(parsed.symbols)) return emptyState()
    const symbols: Record<string, TrendLineDrawing[]> = {}
    for (const [symbol, value] of Object.entries(parsed.symbols)) {
      if (!Array.isArray(value)) continue
      const drawings = value.map(item => normalizeDrawing(item, symbol)).filter(isDefined)
      if (drawings.length > 0) symbols[symbol] = drawings
    }
    return { version: 1, symbols }
  } catch {
    return emptyState()
  }
}

function normalizeDrawing(value: unknown, symbol: string): TrendLineDrawing | undefined {
  if (!isObject(value) || value.kind !== 'trend-line' || value.symbol !== symbol) return undefined
  if (typeof value.id !== 'string' || !Array.isArray(value.anchors) || value.anchors.length !== 2) return undefined
  const first = normalizeAnchor(value.anchors[0])
  const second = normalizeAnchor(value.anchors[1])
  if (!first || !second) return undefined
  const style = isObject(value.style) ? value.style : {}
  return {
    id: value.id,
    kind: 'trend-line',
    symbol,
    anchors: [first, second],
    coordinateMode: value.coordinateMode === 'log' ? 'log' : 'normal',
    style: {
      color: typeof style.color === 'string' ? style.color : defaultTrendLineStyle.color,
      width: style.width === 1 || style.width === 3 ? style.width : 2,
      dash: style.dash === 'dashed' ? 'dashed' : 'solid',
    },
    createdAt: typeof value.createdAt === 'string' ? value.createdAt : '',
    updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : '',
  }
}

function normalizeAnchor(value: unknown): TrendLineAnchor | undefined {
  if (!isObject(value) || typeof value.date !== 'string' || typeof value.price !== 'number' || !Number.isFinite(value.price)) return undefined
  return {
    date: value.date,
    price: value.price,
    snap: value.snap === 'high' || value.snap === 'low' ? value.snap : 'free',
  }
}

function writeState(state: DrawingStoreState, storage: Storage): void {
  storage.setItem(drawingStorageKey, JSON.stringify(state))
}

function notify(symbol: string): void {
  window.dispatchEvent(new CustomEvent(drawingChangeEvent, { detail: { symbol } }))
}

function cloneDrawing(drawing: TrendLineDrawing): TrendLineDrawing {
  return {
    ...drawing,
    anchors: drawing.anchors.map(anchor => ({ ...anchor })) as [TrendLineAnchor, TrendLineAnchor],
    style: { ...drawing.style },
  }
}

function emptyState(): DrawingStoreState {
  return { version: 1, symbols: {} }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isDefined<T>(value: T | undefined): value is T {
  return value !== undefined
}

