import type { PriceMode } from './ChartCanvas'

export type TrendLineSnap = 'free' | 'high' | 'low'
export type TrendLineDash = 'solid' | 'dotted' | 'dashed' | 'long-dashed' | 'dash-dot'

export type DrawingTarget = {
  symbol: string
  instrumentKind?: string
  priceBasis?: string | null
  ruleVersion?: string | null
}

export type TrendLineAnchor = {
  date: string
  price: number
  snap: TrendLineSnap
}

export type TrendLineStyle = {
  color: string
  width: 1 | 2 | 3
  dash: TrendLineDash
}

export type TrendLineDrawing = {
  id: string
  kind: 'trend-line'
  symbol: string
  identityKey: string
  instrumentKind?: string
  priceBasis?: string
  ruleVersion?: string
  anchors: [TrendLineAnchor, TrendLineAnchor]
  coordinateMode: PriceMode
  style: TrendLineStyle
  visible: boolean
  createdAt: string
  updatedAt: string
}

type DrawingStoreState = {
  version: 2
  identities: Record<string, TrendLineDrawing[]>
  legacyFutures: Record<string, unknown[]>
}

const drawingStorageKey = 'stock-harness.drawings.v2'
const legacyDrawingStorageKey = 'stock-harness.drawings.v1'
const drawingChangeEvent = 'stock-harness:drawings-changed'

export const defaultTrendLineStyle: TrendLineStyle = {
  color: '#f0b85a',
  width: 2,
  dash: 'dashed',
}

export function drawingIdentityKey(target: DrawingTarget | string): string | undefined {
  const resolved = normalizeTarget(target)
  if (resolved.instrumentKind === 'futures-continuous') {
    if (!resolved.priceBasis || !resolved.ruleVersion) return undefined
    return [
      'futures-continuous', resolved.symbol,
      resolved.priceBasis, resolved.ruleVersion,
    ].map(encodeURIComponent).join('|')
  }
  if (resolved.instrumentKind === 'futures-contract') {
    return ['futures-contract', resolved.symbol].map(encodeURIComponent).join('|')
  }
  return resolved.symbol
}

export function loadSymbolDrawings(
  target: DrawingTarget | string,
  storage: Storage = window.localStorage,
): TrendLineDrawing[] {
  const key = drawingIdentityKey(target)
  return key ? readState(storage).identities[key]?.map(cloneDrawing) ?? [] : []
}

export function saveTrendLine(
  drawing: TrendLineDrawing,
  storage: Storage = window.localStorage,
): void {
  const state = readState(storage)
  const current = state.identities[drawing.identityKey] ?? []
  const index = current.findIndex(item => item.id === drawing.id)
  const next = current.map(cloneDrawing)
  if (index >= 0) next[index] = cloneDrawing(drawing)
  else next.push(cloneDrawing(drawing))
  state.identities[drawing.identityKey] = next
  writeState(state, storage)
  notify(drawing.identityKey)
}

export function deleteTrendLine(
  target: DrawingTarget | string,
  id: string,
  storage: Storage = window.localStorage,
): void {
  const key = drawingIdentityKey(target)
  if (!key) return
  const state = readState(storage)
  const next = (state.identities[key] ?? []).filter(item => item.id !== id)
  if (next.length === (state.identities[key] ?? []).length) return
  if (next.length > 0) state.identities[key] = next
  else delete state.identities[key]
  writeState(state, storage)
  notify(key)
}

export function subscribeSymbolDrawings(
  target: DrawingTarget | string,
  listener: () => void,
): () => void {
  const key = drawingIdentityKey(target)
  const onCustomChange = (event: Event) => {
    if (key && (event as CustomEvent<{ identityKey?: string }>).detail?.identityKey === key) listener()
  }
  const onStorageChange = (event: StorageEvent) => {
    if (key && event.key === drawingStorageKey) listener()
  }
  window.addEventListener(drawingChangeEvent, onCustomChange)
  window.addEventListener('storage', onStorageChange)
  return () => {
    window.removeEventListener(drawingChangeEvent, onCustomChange)
    window.removeEventListener('storage', onStorageChange)
  }
}

export function subscribeDrawingStore(listener: () => void): () => void {
  const onStorageChange = (event: StorageEvent) => {
    if (event.key === drawingStorageKey) listener()
  }
  window.addEventListener(drawingChangeEvent, listener)
  window.addEventListener('storage', onStorageChange)
  return () => {
    window.removeEventListener(drawingChangeEvent, listener)
    window.removeEventListener('storage', onStorageChange)
  }
}

export function createTrendLine(
  target: DrawingTarget | string,
  anchors: [TrendLineAnchor, TrendLineAnchor],
  coordinateMode: PriceMode,
  now = new Date(),
  createId: () => string = () => crypto.randomUUID(),
): TrendLineDrawing {
  const resolved = normalizeTarget(target)
  const identityKey = drawingIdentityKey(resolved)
  if (!identityKey) throw new Error('continuous futures drawing identity is incomplete')
  const timestamp = now.toISOString()
  return {
    id: createId(),
    kind: 'trend-line',
    symbol: resolved.symbol,
    identityKey,
    instrumentKind: resolved.instrumentKind,
    priceBasis: resolved.priceBasis ?? undefined,
    ruleVersion: resolved.ruleVersion ?? undefined,
    anchors,
    coordinateMode,
    style: { ...defaultTrendLineStyle },
    visible: true,
    createdAt: timestamp,
    updatedAt: timestamp,
  }
}

function readState(storage: Storage): DrawingStoreState {
  const raw = storage.getItem(drawingStorageKey)
  if (raw === null) return migrateLegacyState(storage)
  try {
    const parsed = JSON.parse(raw) as unknown
    if (isObject(parsed) && parsed.version === 2 && isObject(parsed.identities)) {
      const identities: Record<string, TrendLineDrawing[]> = {}
      for (const [key, value] of Object.entries(parsed.identities)) {
        if (!Array.isArray(value)) continue
        const drawings = value.map(item => normalizeDrawing(item, key)).filter(isDefined)
        if (drawings.length > 0) identities[key] = drawings
      }
      return {
        version: 2,
        identities,
        legacyFutures: isObject(parsed.legacyFutures)
          ? Object.fromEntries(
            Object.entries(parsed.legacyFutures).filter(([, value]) => Array.isArray(value)),
          ) as Record<string, unknown[]>
          : {},
      }
    }
  } catch {
    return emptyState()
  }
  return emptyState()
}

function migrateLegacyState(storage: Storage): DrawingStoreState {
  const state = emptyState()
  try {
    const parsed = JSON.parse(storage.getItem(legacyDrawingStorageKey) ?? '') as unknown
    if (!isObject(parsed) || parsed.version !== 1 || !isObject(parsed.symbols)) return state
    for (const [symbol, value] of Object.entries(parsed.symbols)) {
      if (!Array.isArray(value)) continue
      if (isFuturesSymbol(symbol)) {
        state.legacyFutures[symbol] = value
        continue
      }
      const drawings = value.map(item => normalizeLegacyDrawing(item, symbol)).filter(isDefined)
      if (drawings.length > 0) state.identities[symbol] = drawings
    }
    writeState(state, storage)
  } catch {
    return emptyState()
  }
  return state
}

function normalizeDrawing(value: unknown, identityKey: string): TrendLineDrawing | undefined {
  if (!isObject(value) || value.identityKey !== identityKey || typeof value.symbol !== 'string') return undefined
  return normalizeDrawingFields(value, value.symbol, identityKey)
}

function normalizeLegacyDrawing(
  value: unknown,
  symbol: string,
): TrendLineDrawing | undefined {
  if (!isObject(value) || value.symbol !== symbol) return undefined
  return normalizeDrawingFields(value, symbol, symbol)
}

function normalizeDrawingFields(
  value: Record<string, unknown>,
  symbol: string,
  identityKey: string,
): TrendLineDrawing | undefined {
  if (value.kind !== 'trend-line' || typeof value.id !== 'string'
    || !Array.isArray(value.anchors) || value.anchors.length !== 2) return undefined
  const first = normalizeAnchor(value.anchors[0])
  const second = normalizeAnchor(value.anchors[1])
  if (!first || !second) return undefined
  const style = isObject(value.style) ? value.style : {}
  return {
    id: value.id,
    kind: 'trend-line',
    symbol,
    identityKey,
    instrumentKind: typeof value.instrumentKind === 'string' ? value.instrumentKind : undefined,
    priceBasis: typeof value.priceBasis === 'string' ? value.priceBasis : undefined,
    ruleVersion: typeof value.ruleVersion === 'string' ? value.ruleVersion : undefined,
    anchors: [first, second],
    coordinateMode: value.coordinateMode === 'log' ? 'log' : 'normal',
    style: {
      color: typeof style.color === 'string' ? style.color : defaultTrendLineStyle.color,
      width: style.width === 1 || style.width === 3 ? style.width : 2,
      dash: normalizeDash(style.dash),
    },
    visible: value.visible !== false,
    createdAt: typeof value.createdAt === 'string' ? value.createdAt : '',
    updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : '',
  }
}

function normalizeDash(value: unknown): TrendLineDash {
  return value === 'solid' || value === 'dotted' || value === 'long-dashed' || value === 'dash-dot'
    ? value
    : 'dashed'
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

function notify(identityKey: string): void {
  window.dispatchEvent(new CustomEvent(drawingChangeEvent, { detail: { identityKey } }))
}

function cloneDrawing(drawing: TrendLineDrawing): TrendLineDrawing {
  return {
    ...drawing,
    anchors: drawing.anchors.map(anchor => ({ ...anchor })) as [TrendLineAnchor, TrendLineAnchor],
    style: { ...drawing.style },
  }
}

function normalizeTarget(target: DrawingTarget | string): DrawingTarget {
  return typeof target === 'string' ? { symbol: target } : target
}

function isFuturesSymbol(symbol: string): boolean {
  const normalized = symbol.toUpperCase()
  return normalized.startsWith('FUT:') || normalized.startsWith('FUTCONT:')
}

function emptyState(): DrawingStoreState {
  return { version: 2, identities: {}, legacyFutures: {} }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isDefined<T>(value: T | undefined): value is T {
  return value !== undefined
}
