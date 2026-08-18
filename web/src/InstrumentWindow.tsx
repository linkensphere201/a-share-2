import { Maximize2, Minimize2, Pencil, X } from 'lucide-react'
import { ChartCanvas, type ChartIndicator, type VisibleRange } from './ChartCanvas'
import { TradingSystemControls } from './TradingSystemControls'
import type { ChartWindowState } from './workspace'
import type { TradingSystemWindowStates } from './tradingSystems'
import type { ThemeDefinition } from './themeStore'

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
  onTradingSystemsChange: (systems: TradingSystemWindowStates) => void
  onTradingSystemRecalculate: (systemId: string) => void
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
  onTradingSystemsChange,
  onTradingSystemRecalculate,
}: InstrumentWindowProps) {
  const { chart, instrument } = windowState
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
      <div className="instrument-window-body" onPointerDown={onFocus}>
        <TradingSystemControls
          instrumentKind={instrument.kind}
          state={chart.tradingSystems.trend}
          onChange={trend => onTradingSystemsChange({ ...chart.tradingSystems, trend })}
          onRecalculate={() => onTradingSystemRecalculate('trend')}
        />
        <ChartCanvas
          symbol={instrument.symbol}
          lineOnly={instrument.kind === 'custom-index' && chart.seriesMode === 'line'}
          theme={theme}
          range={chart.range}
          priceMode={chart.priceMode}
          volumeVisible={chart.volumeVisible}
          indicator={chart.indicator}
          initialVisibleRange={chart.visibleRange}
          onCoverageChange={onCoverageChange}
          onVisibleRangeChange={onVisibleRangeChange}
          onVolumeVisibleChange={onVolumeVisibleChange}
          onIndicatorChange={onIndicatorChange}
          trendAnalysisEnabled={chart.tradingSystems.trend.enabled}
          showTentativePivots={Boolean(chart.tradingSystems.trend.settings.showTentativePivots)}
          shortTrendLinesVisible={chart.tradingSystems.trend.layers['short-trend-lines'] !== false}
          longTrendLinesVisible={chart.tradingSystems.trend.layers['long-trend-lines'] !== false}
          keyLevelsVisible={chart.tradingSystems.trend.layers['key-levels'] !== false}
          volumeZonesVisible={chart.tradingSystems.trend.layers['volume-zones'] !== false}
        />
      </div>
    </section>
  )
}

export const InstrumentWindow = ChartWindow
