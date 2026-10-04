// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TradeScenarioPanel } from './TradeScenarioPanel'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const run = {
  run_id: 'run-1', as_of_date: '2026-09-09', completion_state: 'complete',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'scenario-1', item_type: 'scenario', payload: {
      kind: 'structural-trade-scenario', primary: true, rank: 1,
      direction: 'long', state: 'triggered', setup_family: 'triangle',
      horizon: 'medium', entry_price: 10, invalidation_price: 9,
      risk_percent: 10, selected_target_label: 'T2', has_trade_space: true,
      evidence_item_ids: ['pattern-1'], invalidation_evidence_item_ids: ['support-1'],
      targets: [
        { label: 'T1', price: 12, basis: 'key-level', risk_reward_ratio: 2,
          stressed_risk_reward_ratio: 1.8, evidence_item_ids: ['target-1'] },
        { label: 'T2', price: 14, basis: 'range-high', risk_reward_ratio: 4,
          stressed_risk_reward_ratio: 3.7, evidence_item_ids: ['target-2'] },
        { label: 'T3', price: 16, basis: 'estimated-volume-at-price', risk_reward_ratio: 6,
          stressed_risk_reward_ratio: 5.4, evidence_item_ids: ['target-3'] },
      ],
    },
  }],
} as TrendAnalysisRun

afterEach(cleanup)

describe('TradeScenarioPanel', () => {
  it('requires an explicit checkbox opt-in when visibility is omitted', () => {
    const onVisibleChange = vi.fn()
    render(<TradeScenarioPanel run={run} onVisibleChange={onVisibleChange} onHighlightItemChange={() => undefined}/>)
    const checkbox = screen.getByRole('checkbox', { name: '显示趋势目标与盈亏比' }) as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    fireEvent.click(checkbox)
    expect(onVisibleChange).toHaveBeenCalledWith(true)
  })

  it('shows one explicit target price, legacy warning, visibility and exact evidence highlights', () => {
    const onTargetChange = vi.fn()
    const onVisibleChange = vi.fn()
    const onHighlightItemChange = vi.fn()
    render(<TradeScenarioPanel
      run={run}
      visible
      onTargetChange={onTargetChange}
      onVisibleChange={onVisibleChange}
      onHighlightItemChange={onHighlightItemChange}
    />)

    expect(screen.getByText('采用目标价')).toBeTruthy()
    expect(screen.getByText('2.00 倍')).toBeTruthy()
    expect(screen.getByText('1.80 倍')).toBeTruthy()
    expect(screen.getByText(/旧版分析，需重新测算/)).toBeTruthy()
    expect(screen.getByText('不可判定')).toBeTruthy()
    expect(screen.queryByText('14.00')).toBeNull()
    expect(screen.queryByText('16.00')).toBeNull()
    expect(onTargetChange).not.toHaveBeenCalled()
    expect(screen.getByText(/三角形/)).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: '显示趋势目标与盈亏比' }))
    expect(onVisibleChange).toHaveBeenCalledWith(false)
    const setup = screen.getByRole('button', { name: /向上场景/ })
    fireEvent.pointerEnter(setup)
    expect(onHighlightItemChange).toHaveBeenCalledWith('pattern-1')
    fireEvent.pointerLeave(setup)
    expect(onHighlightItemChange).toHaveBeenLastCalledWith(undefined)
  })

  it.each(['qualified', 'opportunity', 'insufficient'] as const)('renders backend %s without local threshold logic', status => {
    const current = structuredClone(run)
    Object.assign(current.items[0].payload, {
      contract_version: 'structural-trade-scenario-v5-nearest-raw-rr',
      space_assessment: { policy_version: 'trend-space-v1-raw-strict', status,
        reward_distance: 2, risk_distance: 1 },
    })
    render(<TradeScenarioPanel run={current} onHighlightItemChange={() => undefined}/>)
    expect(screen.getByText({ qualified: '空间合格，观察', opportunity: '满足交易机会的空间门槛',
      insufficient: '空间不合格' }[status])).toBeTruthy()
    expect(screen.getByText('2.0000')).toBeTruthy()
    expect(screen.getByText('1.0000')).toBeTruthy()
    expect(screen.queryByText(/旧版分析/)).toBeNull()
  })
})
