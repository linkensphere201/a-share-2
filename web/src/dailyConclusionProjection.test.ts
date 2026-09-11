import { describe, expect, it } from 'vitest'
import {
  dailyConclusionReferenceFor, mergeDailyConclusionAnalysis,
  type DailyConclusionSource,
} from './dailyConclusionProjection'

const forestry: DailyConclusionSource = {
  runId: 'review-20260910',
  effectiveDate: '2026-09-10',
  stateCodes: ['bullish-boundary-triggered', 'descending-envelope-6m-broken'],
  metrics: {
    atr14: 74.2733,
    descending_envelopes: {
      '6m': {
        boundary: 2585.972791, distance_atr: -0.61515,
        period_bars: 126, slope_per_bar: -8.070409, state: 'broken',
      },
    },
    price_space: {
      kind: 'structural-trade-scenario', scenario_item_id: 'structural-trade-scenario-1',
      direction: 'long', state: 'triggered', setup_family: 'trend-line-break',
      horizon: 'long', start_date: '2026-03-19', reference_price: 2631.662,
      entry_price: 2600.141374, trigger_entry_price: 2600.141374,
      invalidation_price: 2571.804208, risk_percent: 1.0898,
      selected_target_label: 'T1', has_trade_space: true,
      targets: [{
        label: 'T1', price: 3077.6775, basis: 'historical-range-high',
        risk_reward_ratio: 16.851937, stressed_risk_reward_ratio: 14.98838,
      }],
    },
  },
}

describe('daily conclusion projection', () => {
  it('renders canonical current and previous lines for a reanchored boundary', () => {
    const source: DailyConclusionSource = {
      runId: 'forestry-v5', effectiveDate: '2026-09-10', stateCodes: [],
      metrics: { descending_envelopes: { '6m': {
        start_date: '2025-12-30', start_price: 3466.3817,
        end_date: '2026-05-13', end_price: 3077.6775,
        boundary: 2688.9733, state: 'none',
        confirmation_state: 'two-anchor-candidate',
        speed_state: 'decelerating', slope_change_ratio: -.386297,
        previous_line: {
          start_date: '2025-12-30', start_price: 3466.3817,
          end_date: '2026-04-01', end_price: 3032.2247,
        },
      } } },
    }

    const result = mergeDailyConclusionAnalysis(null, source)
    const current = result.run?.items.find(item => item.item_id === 'daily-review:envelope:6m')
    const previous = result.run?.items.find(item => item.item_id === 'daily-review:envelope:6m:previous')

    expect(current?.payload).toMatchObject({
      first_pivot_date: '2025-12-30', first_price: 3466.3817,
      second_pivot_date: '2026-05-13', second_price: 3077.6775,
      projected_price: 2688.9733, speed_state: 'decelerating',
    })
    expect(previous?.payload).toMatchObject({
      first_pivot_date: '2025-12-30', second_pivot_date: '2026-04-01',
      evolution_role: 'previous', superseded_by_line_id: 'daily-review:envelope:6m',
    })
  })

  it('restores legacy first-level line and risk/reward geometry without an M4 run', () => {
    const result = mergeDailyConclusionAnalysis(null, forestry)

    expect(result.run?.run_id).toBe('daily-review:review-20260910')
    expect(result.references).toMatchObject({
      boundaryItemId: 'daily-review:envelope:6m',
      scenarioItemId: 'daily-review:scenario',
      confirmationItemId: 'daily-review:confirmation',
      invalidationItemId: 'daily-review:invalidation',
    })
    const line = result.run?.items.find(item => item.item_id === result.references.boundaryItemId)
    expect(line?.payload).toMatchObject({
      first_pivot_date: '2026-03-19', second_pivot_date: '2026-09-10',
      second_price: 2585.972791, horizon: 'long', kind: 'resistance',
    })
    const scenario = result.run?.items.find(item => item.item_id === result.references.scenarioItemId)
    expect(scenario?.payload).toMatchObject({
      entry_price: 2600.141374, invalidation_price: 2571.804208,
      targets: [{ price: 3077.6775, risk_reward_ratio: 16.851937 }],
    })
    expect(dailyConclusionReferenceFor('space', result.references)).toBe('daily-review:scenario')
    expect(dailyConclusionReferenceFor(
      'hard-event', result.references, 'major-trend-breakout',
    )).toBe('daily-review:envelope:6m')
  })

  it('references an exact M4 scenario when the frozen result already contains it', () => {
    const exact = {
      run_id: 'm4-1', as_of_date: '2026-09-10', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [], items: [{
        item_id: 'structural-trade-scenario-1', item_type: 'scenario' as const,
        payload: { kind: 'structural-trade-scenario' },
      }],
    }
    const result = mergeDailyConclusionAnalysis(exact, forestry)

    expect(result.references.scenarioItemId).toBe('structural-trade-scenario-1')
    expect(result.run?.items.filter(item => item.item_id === 'daily-review:scenario')).toHaveLength(0)
  })
})
