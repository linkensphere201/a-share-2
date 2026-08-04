import { Maximize2, Minimize2, Pencil, X } from 'lucide-react'
import { ChartCanvas, type VisibleRange } from './ChartCanvas'
import type { ChartWindowState } from './workspace'

type InstrumentWindowProps = {
  windowState: ChartWindowState
  focused: boolean
  maximized: boolean
  removable: boolean
  onFocus: () => void
  onToggleMaximize: () => void
  onRemove: () => void
  onEdit: () => void
  onCoverageChange: (rows: number, first?: string, last?: string) => void
  onVisibleRangeChange: (value: VisibleRange) => void
}

export function ChartWindow({
  windowState,
  focused,
  maximized,
  removable,
  onFocus,
  onToggleMaximize,
  onRemove,
  onEdit,
  onCoverageChange,
  onVisibleRangeChange,
}: InstrumentWindowProps) {
  const { chart, instrument } = windowState
  return (
    <section className={focused ? 'instrument-window focused' : 'instrument-window'}>
      <header className="instrument-window-header">
        <button className="instrument-window-title" onClick={onFocus}>
          <strong>{windowState.title}</strong><small>{instrument.name} · {instrument.symbol}</small>
        </button>
        <div className="instrument-window-actions">
          {windowState.mode === 'detached' && <button title="编辑标的" aria-label={`编辑 ${windowState.title} 标的`} onClick={onEdit}><Pencil size={13}/></button>}
          <button
            title={maximized ? '还原窗口' : '最大化窗口'}
            aria-label={maximized ? '还原窗口' : `最大化 ${windowState.title} 窗口`}
            onClick={onToggleMaximize}
          >{maximized ? <Minimize2 size={13}/> : <Maximize2 size={13}/>}</button>
          <button
            title="移除窗口"
            aria-label={`移除 ${windowState.title} 窗口`}
            disabled={!removable}
            onClick={onRemove}
          ><X size={14}/></button>
        </div>
      </header>
      <div className="instrument-window-body" onPointerDown={onFocus}>
        <ChartCanvas
          symbol={instrument.symbol}
          range={chart.range}
          priceMode={chart.priceMode}
          initialVisibleRange={chart.visibleRange}
          onCoverageChange={onCoverageChange}
          onVisibleRangeChange={onVisibleRangeChange}
        />
      </div>
    </section>
  )
}

export const InstrumentWindow = ChartWindow
