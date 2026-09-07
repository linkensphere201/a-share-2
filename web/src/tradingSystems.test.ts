import { describe, expect, it } from 'vitest'
import {
  createTradingSystemWindowStates,
  normalizeTradingSystemWindowStates,
  TradingSystemRegistry,
  trendTradingSystem,
  updateTradingSystemWindowState,
  type TradingSystemDescriptor,
} from './tradingSystems'

const reversalSystem: TradingSystemDescriptor = {
  id: 'reversal-test',
  version: 1,
  name: 'Reversal test',
  supportedInstrumentKinds: ['stock'],
  supportedTimeframes: ['daily'],
  controls: [{ id: 'scan', label: 'Scan', kind: 'command' }],
  layers: [{ id: 'divergence', label: 'Divergence', defaultVisible: false }],
  defaultSettings: { sensitivity: 3 },
  normalizeSettings: value => ({
    sensitivity: typeof (value as { sensitivity?: unknown } | undefined)?.sensitivity === 'number'
      ? Number((value as { sensitivity: number }).sensitivity)
      : 3,
  }),
}

function registryWithTwoSystems(): TradingSystemRegistry {
  const registry = new TradingSystemRegistry()
  registry.register(trendTradingSystem)
  registry.register(reversalSystem)
  return registry
}

describe('trading system registry', () => {
  it('registers independent descriptors and rejects duplicate IDs', () => {
    const registry = registryWithTwoSystems()
    expect(registry.list().map(item => item.id)).toEqual(['trend', 'reversal-test'])
    expect(() => registry.register(reversalSystem)).toThrow('already registered')
  })

  it('normalizes corrupt state through each system-owned schema', () => {
    const registry = registryWithTwoSystems()
    const states = normalizeTradingSystemWindowStates({
      trend: {
        enabled: true,
        expanded: 'invalid',
        layers: { 'short-trend-lines': false, unknown: true },
        settings: { shortHorizonBars: 5000, longHorizonBars: 800 },
      },
      'reversal-test': { enabled: false, layers: { divergence: true }, settings: { sensitivity: 7 } },
    }, registry)

    expect(states.trend.enabled).toBe(true)
    expect(states.trend.expanded).toBe(true)
    expect(states.trend.layers['short-trend-lines']).toBe(false)
    expect(states.trend.layers['medium-trend-lines']).toBe(true)
    expect(states.trend.layers).not.toHaveProperty('unknown')
    expect(states.trend.settings).toMatchObject({
      shortHorizonBars: 120,
      mediumHorizonBars: 140,
      longHorizonBars: 800,
      dailyEnabled: true,
    })
    expect(states['reversal-test']).toMatchObject({ enabled: false, layers: { divergence: true }, settings: { sensitivity: 7 } })
  })

  it('updates one system without mutating another system state', () => {
    const registry = registryWithTwoSystems()
    const initial = createTradingSystemWindowStates(registry)
    const reversalBefore = initial['reversal-test']
    const updated = updateTradingSystemWindowState(initial, 'trend', state => ({
      ...state,
      enabled: true,
      layers: { ...state.layers, patterns: false },
    }), registry)

    expect(updated.trend.enabled).toBe(true)
    expect(updated.trend.layers.patterns).toBe(false)
    expect(updated['reversal-test']).toBe(reversalBefore)
    expect(initial.trend.enabled).toBe(false)
  })

  it('keeps horizons ordered and restores one timeframe for invalid settings', () => {
    const states = normalizeTradingSystemWindowStates({
      trend: {
        settings: {
          shortHorizonBars: 120,
          mediumHorizonBars: 60,
          longHorizonBars: 120,
          dailyEnabled: false,
          weeklyEnabled: false,
          monthlyEnabled: false,
        },
      },
    })

    expect(states.trend.settings).toMatchObject({
      shortHorizonBars: 120,
      mediumHorizonBars: 140,
      longHorizonBars: 160,
      dailyEnabled: true,
      weeklyEnabled: false,
      monthlyEnabled: false,
    })
  })
})
