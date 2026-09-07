import { useEffect, useState } from 'react'
import { Maximize2, Minimize2, PanelTopClose, PanelTopOpen, Pencil, X } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import type { ChartIndicator, VisibleRange } from './chartTypes'
import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import { TradingSystemControls } from './TradingSystemControls'
import { AnalysisWorkspacePanel } from './AnalysisWorkspacePanel'
import type { ChartPaneRatios, ChartWindowState } from './workspace'
import type { TradingSystemWindowState, TradingSystemWindowStates } from './tradingSystems'
import type { ThemeDefinition } from './themeStore'
import { MarketBoardBadge } from './MarketBoardBadge'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import { BoardTagStrip } from './BoardTagStrip'
import { ActiveMarketValueReadout } from './ActiveMarketValueReadout'

type InstrumentWindowProps = {
  windowState: ChartWindowState
  theme: ThemeDefinition
  focused: boolean
  maximized: boolean
  removable: boolean
  poppedOutHost?: boolean
  onFocus: () => void
  onToggleMaximize: () => void
  onRemove: () => void
  onEdit: () => void
  onPopOut: () => void
  onDock: () => void
  onCoverageChange: (rows: number, first?: string, last?: string) => void
  onVisibleRangeChange: (value: VisibleRange) => void
  onVolumeVisibleChange: (visible: boolean) => void
  onIndicatorChange: (indicator: ChartIndicator) => void
  onSettlementVisibleChange: (visible: boolean) => void
  onOpenInterestVisibleChange: (visible: boolean) => void
  onPaneRatiosChange: (ratios: ChartPaneRatios) => void
  onToolbarCollapsedChange: (collapsed: boolean) => void
  onTradingSystemsChange: (systems: TradingSystemWindowStates) => void
  onTradingSystemRecalculate: (
    systemId: string,
    state: TradingSystemWindowState,
    refreshData: boolean,
  ) => Promise<void>
}

export function ChartWindow({
  windowState,
  theme,
  focused,
  maximized,
  removable,
  poppedOutHost = false,
  onFocus,
  onToggleMaximize,
  onRemove,
  onEdit,
  onPopOut,
  onDock,
  onCoverageChange,
  onVisibleRangeChange,
  onVolumeVisibleChange,
  onIndicatorChange,
  onSettlementVisibleChange,
  onOpenInterestVisibleChange,
  onPaneRatiosChange,
  onToolbarCollapsedChange,
  onTradingSystemsChange,
  onTradingSystemRecalculate,
}: InstrumentWindowProps) {
  const { chart, instrument } = windowState
  const [breakoutState, setBreakoutState] = useState<GeneratedBreakoutState>()
  const [trendAnalysis, setTrendAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [viewedTrendAnalysis, setViewedTrendAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [trendRecalculationState, setTrendRecalculationState] = useState<'idle' | 'running' | 'failed'>('idle')
  const [explanationOpen, setExplanationOpen] = useState(false)
  const [highlightedAnalysisItemId, setHighlightedAnalysisItemId] = useState<string>()
  useEffect(() => {
    setBreakoutState(undefined)
    setTrendAnalysis(null)
    setViewedTrendAnalysis(null)
    setTrendRecalculationState('idle')
    setExplanationOpen(false)
    setHighlightedAnalysisItemId(undefined)
  }, [instrument.symbol])
  return (
    <section className={focused ? 'instrument-window focused' : 'instrument-window'}>
      <header className="instrument-window-header">
        <button className="instrument-window-title" onClick={onFocus}>
          <span className="instrument-name-line"><strong>{instrument.name}</strong><MarketBoardBadge instrument={instrument}/></span><small>{instrument.symbol} · {instrument.category ?? instrument.kind}</small>
        </button>
        <BoardTagStrip instrument={instrument} compact/>
        <ActiveMarketValueReadout symbol={instrument.symbol}/>
        <div className="instrument-window-actions">
          <button
            title={poppedOutHost ? '恢复到原布局' : '弹出为独立窗口'}
            aria-label={poppedOutHost ? `恢复 ${instrument.name} 到原布局` : `弹出 ${instrument.name} 为独立窗口`}
            onClick={poppedOutHost ? onDock : onPopOut}
          >{poppedOutHost ? <PanelTopClose size={13}/> : <PanelTopOpen size={13}/>}</button>
          {windowState.mode === 'detached' && <button title="编辑标的" aria-label={`编辑 ${instrument.name} 标的`} onClick={onEdit}><Pencil size={13}/></button>}
          {!poppedOutHost && <><button
            title={maximized ? '还原窗口' : '最大化窗口'}
            aria-label={maximized ? '还原窗口' : `最大化 ${instrument.name} 窗口`}
            onClick={onToggleMaximize}
          >{maximized ? <Minimize2 size={13}/> : <Maximize2 size={13}/>}</button>
          <button
            title="移除窗口"
            aria-label={`移除 ${instrument.name} 窗口`}
            disabled={!removable}
            onClick={onRemove}
          ><X size={14}/></button></>}
        </div>
      </header>
      <div className={explanationOpen ? 'instrument-window-body analysis-open' : 'instrument-window-body'} onPointerDown={onFocus}>
        <ChartCanvas
          symbol={instrument.symbol}
          focused={focused}
          instrumentName={instrument.name}
          instrumentKind={instrument.kind}
          priceBasis={instrument.price_basis}
          ruleVersion={instrument.rule_version}
          lineOnly={instrument.kind === 'custom-index' && chart.seriesMode === 'line'}
          theme={theme}
          range={chart.range}
          priceMode={chart.priceMode}
          volumeVisible={chart.volumeVisible}
          indicator={chart.indicator}
          settlementVisible={chart.settlementVisible}
          openInterestVisible={chart.openInterestVisible}
          paneRatios={chart.paneRatios}
          toolbarCollapsed={chart.drawingToolbarCollapsed}
          toolbarContent={<TradingSystemControls
            embedded
            instrumentKind={instrument.kind}
            state={chart.tradingSystems.trend}
            breakoutState={breakoutState}
            analysisRun={trendAnalysis}
            recalculationState={trendRecalculationState}
            onChange={trend => onTradingSystemsChange({ ...chart.tradingSystems, trend })}
            onRecalculate={async (trend, refreshData) => {
              setTrendRecalculationState('running')
              try {
                await onTradingSystemRecalculate('trend', trend, refreshData)
                setTrendRecalculationState('idle')
              } catch {
                setTrendRecalculationState('failed')
              }
            }}
            explanationOpen={explanationOpen}
            onExplanationOpenChange={open => {
              setExplanationOpen(open)
              if (!open) setHighlightedAnalysisItemId(undefined)
            }}
          />}
          initialVisibleRange={chart.visibleRange}
          onCoverageChange={onCoverageChange}
          onVisibleRangeChange={onVisibleRangeChange}
          onVolumeVisibleChange={onVolumeVisibleChange}
          onIndicatorChange={onIndicatorChange}
          onSettlementVisibleChange={onSettlementVisibleChange}
          onOpenInterestVisibleChange={onOpenInterestVisibleChange}
          onPaneRatiosChange={onPaneRatiosChange}
          onToolbarCollapsedChange={onToolbarCollapsedChange}
          trendAnalysisEnabled={chart.tradingSystems.trend.enabled || explanationOpen}
          showTentativePivots={Boolean(chart.tradingSystems.trend.settings.showTentativePivots)}
          shortTrendLinesVisible={explanationOpen || chart.tradingSystems.trend.layers['short-trend-lines'] !== false}
          mediumTrendLinesVisible={explanationOpen || chart.tradingSystems.trend.layers['medium-trend-lines'] !== false}
          longTrendLinesVisible={explanationOpen || chart.tradingSystems.trend.layers['long-trend-lines'] !== false}
          keyLevelsVisible={explanationOpen || chart.tradingSystems.trend.layers['key-levels'] !== false}
          volumeZonesVisible={chart.tradingSystems.trend.layers['volume-zones'] !== false}
          patternsVisible={explanationOpen || chart.tradingSystems.trend.layers.patterns !== false}
          breakoutStateVisible={chart.tradingSystems.trend.layers['breakout-state'] !== false}
          trendIsolation={chart.tradingSystems.trend.isolate}
          onBreakoutStateChange={setBreakoutState}
          onTrendAnalysisChange={setTrendAnalysis}
          trendAnalysisOverride={viewedTrendAnalysis}
          highlightedAnalysisItemId={highlightedAnalysisItemId}
        />
        {explanationOpen && (viewedTrendAnalysis ?? trendAnalysis) && <AnalysisWorkspacePanel
          symbol={instrument.symbol}
          run={(viewedTrendAnalysis ?? trendAnalysis)!}
          followingLatest={viewedTrendAnalysis === null}
          onRunChange={setViewedTrendAnalysis}
          onHighlightItemChange={setHighlightedAnalysisItemId}
          onClose={() => {
            setExplanationOpen(false)
            setHighlightedAnalysisItemId(undefined)
          }}
        />}
      </div>
    </section>
  )
}

export const InstrumentWindow = ChartWindow
