export type TradingSystemControlKind = 'command' | 'toggle' | 'layer-group'

export type TradingSystemControlDefinition = {
  id: string
  label: string
  kind: TradingSystemControlKind
}

export type TradingSystemLayerDefinition = {
  id: string
  label: string
  defaultVisible: boolean
}

export type TradingSystemDescriptor = {
  id: string
  version: number
  name: string
  supportedInstrumentKinds: readonly string[]
  supportedTimeframes: readonly string[]
  controls: readonly TradingSystemControlDefinition[]
  layers: readonly TradingSystemLayerDefinition[]
  defaultSettings: Readonly<Record<string, boolean | number | string>>
  normalizeSettings: (value: unknown) => Record<string, boolean | number | string>
}

export type TradingSystemWindowState = {
  version: 1
  enabled: boolean
  expanded: boolean
  isolate: boolean
  layers: Record<string, boolean>
  settings: Record<string, boolean | number | string>
}

export type TradingSystemWindowStates = Record<string, TradingSystemWindowState>

export class TradingSystemRegistry {
  private readonly descriptors = new Map<string, TradingSystemDescriptor>()

  register(descriptor: TradingSystemDescriptor): void {
    if (!descriptor.id || descriptor.version < 1) throw new Error('invalid trading system descriptor')
    if (this.descriptors.has(descriptor.id)) throw new Error(`trading system already registered: ${descriptor.id}`)
    this.descriptors.set(descriptor.id, descriptor)
  }

  get(id: string): TradingSystemDescriptor | undefined {
    return this.descriptors.get(id)
  }

  list(): TradingSystemDescriptor[] {
    return [...this.descriptors.values()]
  }
}

const trendSettings = {
  shortHorizonBars: 60,
  longHorizonBars: 250,
}

export const trendTradingSystem: TradingSystemDescriptor = {
  id: 'trend',
  version: 1,
  name: '趋势交易体系',
  supportedInstrumentKinds: ['stock', 'etf', 'index', 'sector', 'custom-index'],
  supportedTimeframes: ['daily', 'weekly', 'monthly'],
  controls: [
    { id: 'analyze', label: '更新测算', kind: 'command' },
    { id: 'visibility', label: '显示分析', kind: 'toggle' },
    { id: 'layers', label: '分析图层', kind: 'layer-group' },
  ],
  layers: [
    { id: 'short-trend-lines', label: '短期趋势线', defaultVisible: true },
    { id: 'long-trend-lines', label: '长期趋势线', defaultVisible: true },
    { id: 'key-levels', label: '关键位', defaultVisible: true },
    { id: 'volume-zones', label: '成交密集区', defaultVisible: true },
    { id: 'patterns', label: '形态', defaultVisible: true },
    { id: 'breakout-state', label: '突破与破位', defaultVisible: true },
  ],
  defaultSettings: trendSettings,
  normalizeSettings: value => {
    const record = isRecord(value) ? value : {}
    return {
      shortHorizonBars: boundedInteger(record.shortHorizonBars, trendSettings.shortHorizonBars, 20, 250),
      longHorizonBars: boundedInteger(record.longHorizonBars, trendSettings.longHorizonBars, 120, 1250),
    }
  },
}

export const tradingSystemRegistry = new TradingSystemRegistry()
tradingSystemRegistry.register(trendTradingSystem)

export function createTradingSystemWindowState(
  descriptor: TradingSystemDescriptor,
): TradingSystemWindowState {
  return {
    version: 1,
    enabled: false,
    expanded: true,
    isolate: false,
    layers: Object.fromEntries(descriptor.layers.map(layer => [layer.id, layer.defaultVisible])),
    settings: { ...descriptor.defaultSettings },
  }
}

export function createTradingSystemWindowStates(
  registry: TradingSystemRegistry = tradingSystemRegistry,
): TradingSystemWindowStates {
  return Object.fromEntries(registry.list().map(descriptor => [
    descriptor.id,
    createTradingSystemWindowState(descriptor),
  ]))
}

export function normalizeTradingSystemWindowStates(
  value: unknown,
  registry: TradingSystemRegistry = tradingSystemRegistry,
): TradingSystemWindowStates {
  const source = isRecord(value) ? value : {}
  return Object.fromEntries(registry.list().map(descriptor => {
    const sourceCandidate = source[descriptor.id]
    const candidate: Record<string, unknown> = isRecord(sourceCandidate) ? sourceCandidate : {}
    const candidateLayers: Record<string, unknown> = isRecord(candidate.layers) ? candidate.layers : {}
    const defaults = createTradingSystemWindowState(descriptor)
    return [descriptor.id, {
      version: 1,
      enabled: typeof candidate.enabled === 'boolean' ? candidate.enabled : defaults.enabled,
      expanded: typeof candidate.expanded === 'boolean' ? candidate.expanded : defaults.expanded,
      isolate: typeof candidate.isolate === 'boolean' ? candidate.isolate : defaults.isolate,
      layers: Object.fromEntries(descriptor.layers.map(layer => [
        layer.id,
        typeof candidateLayers[layer.id] === 'boolean'
          ? candidateLayers[layer.id] as boolean
          : layer.defaultVisible,
      ])),
      settings: descriptor.normalizeSettings(candidate.settings),
    } satisfies TradingSystemWindowState]
  }))
}

export function updateTradingSystemWindowState(
  states: TradingSystemWindowStates,
  systemId: string,
  update: (state: TradingSystemWindowState) => TradingSystemWindowState,
  registry: TradingSystemRegistry = tradingSystemRegistry,
): TradingSystemWindowStates {
  const descriptor = registry.get(systemId)
  if (!descriptor) return states
  const current = normalizeTradingSystemWindowStates(states, registry)[systemId]
  return { ...states, [systemId]: update(current) }
}

function boundedInteger(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === 'number' && Number.isInteger(value)
    ? Math.min(maximum, Math.max(minimum, value))
    : fallback
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
