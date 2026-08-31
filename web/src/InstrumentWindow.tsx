import { useEffect, useMemo, useState } from 'react'
import { Maximize2, Minimize2, Pencil, X } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import type { ChartIndicator, VisibleRange } from './chartTypes'
import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import { TradingSystemControls } from './TradingSystemControls'
import { TrendReviewPanel } from './TrendReviewPanel'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import { AiAnalysisPanel } from './AiAnalysisPanel'
import { loadLatestAiAnalysis, projectAiReferences, type AiAnalysisReport } from './aiAnalysisClient'
import type { ChartPaneRatios, ChartWindowState } from './workspace'
import type { TradingSystemWindowState, TradingSystemWindowStates } from './tradingSystems'
import { normalizeTrendTradingSystemSettings } from './tradingSystems'
import type { ThemeDefinition } from './themeStore'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import type { TrendReviewGeometryTarget } from './trendReviewGeometry'

type InstrumentWindowProps = {
  windowState: ChartWindowState
  theme: ThemeDefinition
  focused: boolean
  maximized: boolean
  removable: boolean
  onFocus: () => void
  onToggleMaximize: () => void
  onRemove: () => void
  onEdit: () => void
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
  onFocus,
  onToggleMaximize,
  onRemove,
  onEdit,
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
  const [trendRecalculationState, setTrendRecalculationState] = useState<'idle' | 'running' | 'failed'>('idle')
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewContext, setReviewContext] = useState<{
    asOfDate: string
    analysis: TrendAnalysisRun
  }>()
  const [reviewGeometryTarget, setReviewGeometryTarget] = useState<TrendReviewGeometryTarget>()
  const [explanationOpen, setExplanationOpen] = useState(false)
  const [aiAnalysisOpen, setAiAnalysisOpen] = useState(false)
  const [aiAnalysisReport, setAiAnalysisReport] = useState<AiAnalysisReport | null>(null)
  const [highlightedAnalysisItemId, setHighlightedAnalysisItemId] = useState<string>()
  const aiAnalysisItems = useMemo(
    () => projectAiReferences(aiAnalysisReport),
    [aiAnalysisReport],
  )
  useEffect(() => {
    setBreakoutState(undefined)
    setTrendAnalysis(null)
    setTrendRecalculationState('idle')
    setReviewOpen(false)
    setReviewContext(undefined)
    setReviewGeometryTarget(undefined)
    setExplanationOpen(false)
    setAiAnalysisOpen(false)
    setAiAnalysisReport(null)
    setHighlightedAnalysisItemId(undefined)
  }, [instrument.symbol])
  const openAiAnalysis = async (open: boolean) => {
    setAiAnalysisOpen(open)
    setExplanationOpen(false)
    setHighlightedAnalysisItemId(undefined)
    if (!open) return
    try {
      setAiAnalysisReport(await loadLatestAiAnalysis(instrument.symbol, 'daily'))
    } catch {
      setAiAnalysisReport(null)
    }
  }
  return (
    <section className={focused ? 'instrument-window focused' : 'instrument-window'}>
      <header className="instrument-window-header">
        <button className="instrument-window-title" onClick={onFocus}>
          <strong>{instrument.name}</strong><small>{instrument.symbol} · {instrument.category ?? instrument.kind}</small>
        </button>
        <div className="instrument-window-actions">
          {windowState.mode === 'detached' && <button title="编辑标的" aria-label={`编辑 ${instrument.name} 标的`} onClick={onEdit}><Pencil size={13}/></button>}
          <button
            title={maximized ? '还原窗口' : '最大化窗口'}
            aria-label={maximized ? '还原窗口' : `最大化 ${instrument.name} 窗口`}
            onClick={onToggleMaximize}
          >{maximized ? <Minimize2 size={13}/> : <Maximize2 size={13}/>}</button>
          <button
            title="移除窗口"
            aria-label={`移除 ${instrument.name} 窗口`}
            disabled={!removable}
            onClick={onRemove}
          ><X size={14}/></button>
        </div>
      </header>
      <div className={explanationOpen || aiAnalysisOpen ? 'instrument-window-body explanation-open' : 'instrument-window-body'} onPointerDown={onFocus}>
        <TradingSystemControls
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
          onReview={() => setReviewOpen(true)}
          explanationOpen={explanationOpen}
          onExplanationOpenChange={open => {
            setExplanationOpen(open)
            if (open) setAiAnalysisOpen(false)
            if (!open) setHighlightedAnalysisItemId(undefined)
          }}
          aiAnalysisAvailable
          aiAnalysisOpen={aiAnalysisOpen}
          onAiAnalysisOpenChange={open => void openAiAnalysis(open)}
        />
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
          initialVisibleRange={chart.visibleRange}
          onCoverageChange={onCoverageChange}
          onVisibleRangeChange={onVisibleRangeChange}
          onVolumeVisibleChange={onVolumeVisibleChange}
          onIndicatorChange={onIndicatorChange}
          onSettlementVisibleChange={onSettlementVisibleChange}
          onOpenInterestVisibleChange={onOpenInterestVisibleChange}
          onPaneRatiosChange={onPaneRatiosChange}
          onToolbarCollapsedChange={onToolbarCollapsedChange}
          trendAnalysisEnabled={chart.tradingSystems.trend.enabled || aiAnalysisOpen}
          showTentativePivots={Boolean(chart.tradingSystems.trend.settings.showTentativePivots)}
          shortTrendLinesVisible={aiAnalysisOpen || chart.tradingSystems.trend.layers['short-trend-lines'] !== false}
          longTrendLinesVisible={aiAnalysisOpen || chart.tradingSystems.trend.layers['long-trend-lines'] !== false}
          keyLevelsVisible={aiAnalysisOpen || chart.tradingSystems.trend.layers['key-levels'] !== false}
          volumeZonesVisible={chart.tradingSystems.trend.layers['volume-zones'] !== false}
          patternsVisible={aiAnalysisOpen || chart.tradingSystems.trend.layers.patterns !== false}
          breakoutStateVisible={chart.tradingSystems.trend.layers['breakout-state'] !== false}
          trendIsolation={chart.tradingSystems.trend.isolate || Boolean(reviewContext)}
          asOfDate={reviewContext?.asOfDate}
          trendAnalysisOverride={reviewContext?.analysis}
          reviewGeometryTarget={reviewGeometryTarget}
          onBreakoutStateChange={setBreakoutState}
          onTrendAnalysisChange={setTrendAnalysis}
          highlightedAnalysisItemId={highlightedAnalysisItemId}
          supplementalAnalysisItems={aiAnalysisItems}
          supplementalAnalysisOnly={aiAnalysisOpen && aiAnalysisItems.length > 0}
        />
        {explanationOpen && trendAnalysis && <TrendExplanationPanel
          run={trendAnalysis}
          onHighlightItemChange={setHighlightedAnalysisItemId}
          onClose={() => {
            setExplanationOpen(false)
            setHighlightedAnalysisItemId(undefined)
          }}
        />}
        {aiAnalysisOpen && <AiAnalysisPanel
          report={aiAnalysisReport}
          onHighlightItemChange={setHighlightedAnalysisItemId}
          onClose={() => {
            setAiAnalysisOpen(false)
            setHighlightedAnalysisItemId(undefined)
          }}
        />}
        {reviewOpen && <TrendReviewPanel
          symbol={instrument.symbol}
          name={instrument.name}
          settings={normalizeTrendTradingSystemSettings(chart.tradingSystems.trend.settings)}
          currentAnalysis={trendAnalysis}
          onContextChange={setReviewContext}
          onGeometryTargetChange={setReviewGeometryTarget}
          onClose={() => {
            setReviewOpen(false)
            setReviewContext(undefined)
            setReviewGeometryTarget(undefined)
          }}
        />}
      </div>
    </section>
  )
}

export const InstrumentWindow = ChartWindow
