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

afterEach(cleanup)

function Harness({
  initial = createTradingSystemWindowStates().trend,
  onRecalculate = vi.fn(),
}: {
  initial?: TradingSystemWindowState
  onRecalculate?: (state: TradingSystemWindowState) => void
}) {
  const [state, setState] = useState(initial)
  return <>
    <TradingSystemControls instrumentKind="stock" state={state} onChange={setState} onRecalculate={onRecalculate}/>
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
    expect(onRecalculate).toHaveBeenCalledWith(expect.objectContaining({ analysisStatus: 'stale' }))
  })
})
