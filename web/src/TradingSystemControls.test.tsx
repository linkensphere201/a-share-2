// @vitest-environment jsdom

import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TradingSystemControls } from './TradingSystemControls'
import {
  createTradingSystemWindowStates,
  type TradingSystemWindowState,
} from './tradingSystems'
import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

afterEach(cleanup)

function Harness({
  initial = createTradingSystemWindowStates().trend,
  onRecalculate = vi.fn(),
  breakoutState,
  analysisRun,
  onExplanationOpenChange,
}: {
  initial?: TradingSystemWindowState
  onRecalculate?: (state: TradingSystemWindowState, refreshData: boolean) => void
  breakoutState?: GeneratedBreakoutState
  analysisRun?: TrendAnalysisRun
  onExplanationOpenChange?: (open: boolean) => void
}) {
  const [state, setState] = useState(initial)
  return <>
    <TradingSystemControls instrumentKind="stock" state={state} breakoutState={breakoutState} analysisRun={analysisRun} onChange={setState} onRecalculate={onRecalculate} onExplanationOpenChange={onExplanationOpenChange}/>
    <output data-testid="state">{JSON.stringify(state)}</output>
  </>
}

describe('TradingSystemControls', () => {
  it('edits validated settings and keeps the remaining timeframe selected', async () => {
    const user = userEvent.setup()
    render(<Harness/>)

    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: '短期交易日' }), { target: { value: '120' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '中期交易日' }), { target: { value: '60' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '长期交易日' }), { target: { value: '120' } })
    await user.click(screen.getByRole('checkbox', { name: '分析周线' }))
    await user.click(screen.getByRole('checkbox', { name: '分析月线' }))
    await user.click(screen.getByRole('checkbox', { name: '分析日线' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    const state = JSON.parse(screen.getByTestId('state').textContent ?? '{}')
    expect(state.settings).toMatchObject({
      shortHorizonBars: 120,
      mediumHorizonBars: 140,
      longHorizonBars: 160,
      dailyEnabled: true,
      weeklyEnabled: false,
      monthlyEnabled: false,
    })
    expect(state.settingsRevision).toBe(1)
  })

  it('cancels drafts and restores defaults before saving', async () => {
    const user = userEvent.setup()
    render(<Harness/>)

    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: '短期交易日' }), { target: { value: '90' } })
    await user.click(screen.getByRole('button', { name: '取消' }))
    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    expect(screen.getByRole('spinbutton', { name: '短期交易日' })).toHaveProperty('value', '60')

    fireEvent.change(screen.getByRole('spinbutton', { name: '短期交易日' }), { target: { value: '90' } })
    await user.click(screen.getByRole('button', { name: '恢复趋势体系默认设置' }))
    expect(screen.getByRole('spinbutton', { name: '短期交易日' })).toHaveProperty('value', '60')
  })

  it('marks a current result stale and dispatches save-and-recalculate', async () => {
    const user = userEvent.setup()
    const onRecalculate = vi.fn()
    const initial = { ...createTradingSystemWindowStates().trend, enabled: true, analysisStatus: 'current' as const }
    render(<Harness initial={initial} onRecalculate={onRecalculate}/>)

    await user.click(screen.getByRole('button', { name: '趋势交易体系设置' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: '短期交易日' }), { target: { value: '80' } })
    await user.click(screen.getByRole('button', { name: '保存并重新测算' }))

    const state = JSON.parse(screen.getByTestId('state').textContent ?? '{}')
    expect(state.analysisStatus).toBe('stale')
    expect(onRecalculate).toHaveBeenCalledWith(
      expect.objectContaining({ analysisStatus: 'stale', settingsRevision: 1 }),
      false,
    )
  })

  it('shows recalculation progress and prevents duplicate requests', () => {
    const initial = { ...createTradingSystemWindowStates().trend, enabled: true }
    const onRecalculate = vi.fn()
    render(<TradingSystemControls
      instrumentKind="stock"
      state={initial}
      recalculationState="running"
      onChange={vi.fn()}
      onRecalculate={onRecalculate}
    />)

    expect(screen.getByText('正在测算')).toBeTruthy()
    const refresh = screen.getByRole('button', { name: '更新测算' })
    expect(refresh).toHaveProperty('disabled', true)
    fireEvent.click(refresh)
    expect(onRecalculate).not.toHaveBeenCalled()
  })

  it('shows the latest structural event directly on the recalculate control', () => {
    const initial = { ...createTradingSystemWindowStates().trend, enabled: true }
    const analysisRun: TrendAnalysisRun = {
      run_id: 'run', as_of_date: '2026-08-18', completion_state: 'partial',
      source_observed_at_ms: 123, stale: false, stale_reasons: [], warnings: [],
      items: [{ item_id: 'pattern', item_type: 'pattern', payload: {
        primary: true, display_name: '双底', timeframe: 'daily', score: 0.8,
        score_components: { symmetry: 0.9 },
      } }],
    }
    const onExplanationOpenChange = vi.fn()
    render(<Harness initial={initial} analysisRun={analysisRun} onExplanationOpenChange={onExplanationOpenChange} breakoutState={{
      state: 'failed', direction: 'up', boundaryPrice: 12,
      invalidationPrice: 11.5, preview: true,
      eventKind: 'false-breakout-risk',
    }}/>)

    const dot = screen.getByTestId('trend-recalculate-event')
    const button = dot.closest('button')
    expect(button?.getAttribute('data-event-label')).toBe('假突破风险')
    expect(button?.getAttribute('title')).toBe('更新测算 · 盘中预览 · 假突破风险')
    expect(button?.classList.contains('failed')).toBe(true)

    fireEvent.click(dot)
    expect(onExplanationOpenChange).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: '查看趋势证据' })).toBeNull()
    expect(screen.queryByRole('button', { name: '打开趋势人工复核' })).toBeNull()
  })

  it('toggles one-click trend isolation without changing analytical settings', async () => {
    const user = userEvent.setup()
    const initial = { ...createTradingSystemWindowStates().trend, enabled: true }
    render(<Harness initial={initial}/>)

    await user.click(screen.getByRole('button', { name: '仅查看趋势体系' }))
    let state = JSON.parse(screen.getByTestId('state').textContent ?? '{}')
    expect(state.isolate).toBe(true)
    expect(state.settings).toEqual(initial.settings)

    await user.click(screen.getByRole('button', { name: '退出趋势隔离' }))
    state = JSON.parse(screen.getByTestId('state').textContent ?? '{}')
    expect(state.isolate).toBe(false)
  })

  it('collapses to its single expansion control without losing window state', async () => {
    const user = userEvent.setup()
    const initial = {
      ...createTradingSystemWindowStates().trend,
      enabled: true,
      isolate: true,
    }
    const { container } = render(<Harness initial={initial}/>)

    await user.click(screen.getByRole('button', { name: '收起趋势交易体系' }))

    expect(container.querySelector('.trading-system-controls')?.classList.contains('collapsed')).toBe(true)
    expect(screen.getByRole('button', { name: '展开趋势交易体系' }).getAttribute('aria-expanded')).toBe('false')
    const state = JSON.parse(screen.getByTestId('state').textContent ?? '{}')
    expect(state).toMatchObject({ expanded: false, enabled: true, isolate: true })
  })

  it('renders as an always-expanded toolbar group when embedded in the chart toolbar', () => {
    const state = { ...createTradingSystemWindowStates().trend, expanded: false }
    const { container } = render(<TradingSystemControls
      embedded
      instrumentKind="stock"
      state={state}
      onChange={vi.fn()}
      onRecalculate={vi.fn()}
    />)

    expect(container.querySelector('.trading-system-controls')?.classList.contains('embedded')).toBe(true)
    expect(container.querySelector('.trading-system-controls')?.classList.contains('collapsed')).toBe(false)
    expect(screen.queryByRole('button', { name: '展开趋势交易体系' })).toBeNull()
    expect(screen.getByRole('button', { name: '启用趋势交易体系' })).toBeTruthy()
  })

  it('opens the result explanation only when analysis exists', () => {
    const state = { ...createTradingSystemWindowStates().trend, enabled: true }
    const onOpenChange = vi.fn()
    const { rerender } = render(<TradingSystemControls
      instrumentKind="stock"
      state={state}
      onChange={vi.fn()}
      onRecalculate={vi.fn()}
      onExplanationOpenChange={onOpenChange}
    />)
    const unavailable = screen.getByRole('button', { name: '打开形态分析结果' })
    expect(unavailable).toHaveProperty('disabled', true)

    rerender(<TradingSystemControls
      instrumentKind="stock"
      state={state}
      analysisRun={{
        run_id: 'run', as_of_date: '2026-08-21', completion_state: 'complete',
        stale: false, stale_reasons: [], warnings: [], items: [],
      }}
      onChange={vi.fn()}
      onRecalculate={vi.fn()}
      onExplanationOpenChange={onOpenChange}
    />)
    const available = screen.getByRole('button', { name: '打开形态分析结果' })
    expect(available).toHaveProperty('disabled', false)
    fireEvent.click(available)
    expect(onOpenChange).toHaveBeenCalledWith(true)
  })
})
