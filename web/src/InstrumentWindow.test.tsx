// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { ComponentProps, ReactNode } from 'react'
import { ChartWindow } from './InstrumentWindow'
import { createDefaultWorkspace, type ChartWindowState } from './workspace'
import { themes } from './themeStore'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import { analysisLayers, type AnalysisLayerVisibility } from './AnalysisOverlayToggle'

vi.mock('./BoardTagStrip', () => ({ BoardTagStrip: () => null }))
vi.mock('./ActiveMarketValueReadout', () => ({ ActiveMarketValueReadout: () => null }))
vi.mock('./ChartCanvas', () => ({ ChartCanvas: ({ riskRewardVisible, toolbarContent, onTrendAnalysisChange, ...layers }: AnalysisLayerVisibility & {
  riskRewardVisible: boolean; toolbarContent: ReactNode; onTrendAnalysisChange: (run: TrendAnalysisRun) => void
}) => <div data-testid="main-chart" data-layers={JSON.stringify(layers)} data-targets={String(riskRewardVisible)}>
  {toolbarContent}
  <button onClick={() => onTrendAnalysisChange(analysis('run-1'))}>加载分析</button>
  <button onClick={() => onTrendAnalysisChange(analysis('run-2'))}>更新分析</button>
</div> }))
vi.mock('./AnalysisWorkspacePanel', async () => {
  const { TrendExplanationPanel } = await import('./TrendExplanationPanel')
  return { AnalysisWorkspacePanel: (props: ComponentProps<typeof TrendExplanationPanel>) => <TrendExplanationPanel {...props} embedded/> }
})

afterEach(cleanup)

function analysis(runId: string): TrendAnalysisRun {
  return { run_id: runId, as_of_date: '2026-09-15', completion_state: 'complete',
    stale: false, stale_reasons: [], warnings: [], items: [] }
}

it('main analysis uses an unchecked shared checkbox despite legacy saved visibility', () => {
  const state = createDefaultWorkspace().groups[0].windows.find(window => window.type === 'chart') as ChartWindowState
  state.chart.riskRewardVisible = true
  const noop = () => undefined
  const props: ComponentProps<typeof ChartWindow> = {
    windowState: state, theme: themes[0], focused: true, maximized: false, removable: true,
    onFocus: noop, onToggleMaximize: noop, onRemove: noop, onEdit: noop, onPopOut: noop, onDock: noop,
    onCoverageChange: noop, onVisibleRangeChange: noop, onVolumeVisibleChange: noop,
    onIndicatorChange: noop, onSettlementVisibleChange: noop, onOpenInterestVisibleChange: noop,
    onPaneRatiosChange: noop, onToolbarCollapsedChange: noop, onScenarioTargetChange: noop,
    onTradingSystemsChange: noop, onTradingSystemRecalculate: async () => undefined,
  }
  const view = render(<ChartWindow {...props}/>)
  expect(screen.getByTestId('main-chart').dataset.targets).toBe('false')
  fireEvent.click(screen.getByText('加载分析'))
  fireEvent.click(screen.getByRole('button', { name: '打开形态分析结果' }))
  const toggle = screen.getByRole('checkbox', { name: '显示趋势目标与盈亏比' }) as HTMLInputElement
  expect(toggle.checked).toBe(false)
  for (const [key, label] of Object.entries(analysisLayers)) {
    const layerToggle = screen.getByRole('checkbox', { name: `显示${label}` }) as HTMLInputElement
    expect(layerToggle.checked).toBe(false)
    expect(JSON.parse(screen.getByTestId('main-chart').dataset.layers!)[key]).toBe(false)
    fireEvent.click(layerToggle)
    expect(JSON.parse(screen.getByTestId('main-chart').dataset.layers!)[key]).toBe(true)
  }
  fireEvent.click(toggle)
  expect(screen.getByTestId('main-chart').dataset.targets).toBe('true')
  fireEvent.click(toggle)
  expect(screen.getByTestId('main-chart').dataset.targets).toBe('false')
  fireEvent.click(toggle)
  fireEvent.click(screen.getByText('更新分析'))
  for (const key of Object.keys(analysisLayers)) {
    expect(JSON.parse(screen.getByTestId('main-chart').dataset.layers!)[key]).toBe(false)
  }
  expect(toggle.checked).toBe(false)
  expect(screen.getByTestId('main-chart').dataset.targets).toBe('false')
  fireEvent.click(toggle)
  view.rerender(<ChartWindow {...props} windowState={{ ...state, instrument: { ...state.instrument, symbol: '000002.SZ' } }}/>)
  expect(screen.getByTestId('main-chart').dataset.targets).toBe('false')
})
