import type { ChartIndicator, VisibleRange } from './chartTypes'
import { ChartWindow } from './InstrumentWindow'
import { InstrumentListWindow } from './InstrumentListWindow'
import { MarketBoardBadge } from './MarketBoardBadge'
import { SplitLayout } from './SplitLayout'
import type { ChartPaneRatios, Instrument, ListColumnKey, WindowGroupState } from './workspace'
import type { ThemeDefinition } from './themeStore'
import type { TradingSystemWindowState, TradingSystemWindowStates } from './tradingSystems'
import { PanelTopClose } from 'lucide-react'

type WindowGroupProps = {
  group: WindowGroupState
  theme: ThemeDefinition
  onFocusWindow: (id: string) => void
  onToggleMaximize: (id: string) => void
  onRemoveWindow: (id: string) => void
  onResizeSplit: (id: string, ratio: number) => void
  onSelectListInstrument: (id: string, instrument: Instrument) => void
  onDeleteListInstrument: (id: string, instrument: Instrument) => void
  onTemporaryCast: (chartId: string, instrument: Instrument) => void
  onEditWindow: (id: string) => void
  onSortList: (id: string, sort: NonNullable<Extract<WindowGroupState['windows'][number], { type: 'instrument-list' }>['sort']>) => void
  onListColumnsChange: (id: string, columns: ListColumnKey[]) => void
  onCoverageChange: (id: string, symbol: string, rows: number, first?: string, last?: string) => void
  onVisibleRangeChange: (id: string, value: VisibleRange) => void
  onVolumeVisibleChange: (id: string, visible: boolean) => void
  onIndicatorChange: (id: string, indicator: ChartIndicator) => void
  onSettlementVisibleChange: (id: string, visible: boolean) => void
  onOpenInterestVisibleChange: (id: string, visible: boolean) => void
  onPaneRatiosChange: (id: string, ratios: ChartPaneRatios) => void
  onToolbarCollapsedChange: (id: string, collapsed: boolean) => void
  onRiskRewardVisibleChange: (id: string, visible: boolean) => void
  onScenarioTargetChange: (id: string, target?: string) => void
  onTradingSystemsChange: (id: string, systems: TradingSystemWindowStates) => void
  onTradingSystemRecalculate: (
    id: string,
    systemId: string,
    state: TradingSystemWindowState,
    refreshData: boolean,
  ) => Promise<void>
  onReferencedSymbolsChange: (id: string, symbols: string[]) => void
  renderOnlyWindowId?: string
  poppedOutHost?: boolean
  onPopOutWindow: (id: string) => void
  onDockWindow: (id: string) => void
  onFocusPopoutWindow: (id: string) => void
}

export function WindowGroup({
  group,
  theme,
  onFocusWindow,
  onToggleMaximize,
  onRemoveWindow,
  onResizeSplit,
  onSelectListInstrument,
  onDeleteListInstrument,
  onTemporaryCast,
  onEditWindow,
  onSortList,
  onListColumnsChange,
  onCoverageChange,
  onVisibleRangeChange,
  onVolumeVisibleChange,
  onIndicatorChange,
  onSettlementVisibleChange,
  onOpenInterestVisibleChange,
  onPaneRatiosChange,
  onToolbarCollapsedChange,
  onRiskRewardVisibleChange,
  onScenarioTargetChange,
  onTradingSystemsChange,
  onTradingSystemRecalculate,
  onReferencedSymbolsChange,
  renderOnlyWindowId,
  poppedOutHost = false,
  onPopOutWindow,
  onDockWindow,
  onFocusPopoutWindow,
}: WindowGroupProps) {
  const renderWindow = (windowId: string) => {
    const item = group.windows.find(window => window.id === windowId)
    if (!item) return null
    if (!poppedOutHost && item.presentation.mode === 'popped-out') {
      return <div className="popped-out-placeholder" data-window-id={item.id}>
        <div>
          <button title="显示已弹出窗口" aria-label={`显示已弹出的 ${item.title}`} onClick={() => onFocusPopoutWindow(item.id)}>
            <PanelTopClose size={14}/>{item.type === 'chart'
              ? <span className="instrument-name-line"><span>{item.instrument.name}</span><MarketBoardBadge instrument={item.instrument}/></span>
              : <span>{item.title}</span>}
          </button>
          <button className="restore-popout-placeholder" onClick={() => onDockWindow(item.id)}>
            复原到布局
          </button>
        </div>
      </div>
    }
    if (item.type === 'instrument-list') {
      const incoming = group.attachments.filter(edge => edge.type === 'show-members' && edge.targetWindowId === item.id)
      const sourceEdge = incoming.find(edge => edge.sourceWindowId === item.memberSourceWindowId) ?? incoming[0]
      const sourceWindow = group.windows.find(window => window.id === sourceEdge?.sourceWindowId)
      const memberSource = sourceWindow?.type === 'instrument-list'
        ? sourceWindow.content.instruments.find(instrument => instrument.symbol === sourceWindow.selectedSymbol)
        : undefined
      return (
        <InstrumentListWindow
          windowState={item}
          focused={group.focusedWindowId === item.id}
          maximized={group.maximizedWindowId === item.id}
          removable={group.windows.length > 1}
          poppedOutHost={poppedOutHost}
          onFocus={() => onFocusWindow(item.id)}
          onToggleMaximize={() => onToggleMaximize(item.id)}
          onRemoveWindow={() => onRemoveWindow(item.id)}
          onSelect={instrument => onSelectListInstrument(item.id, instrument)}
          onDeleteInstrument={instrument => onDeleteListInstrument(item.id, instrument)}
          onTemporaryCast={onTemporaryCast}
          chartTargets={group.windows
            .filter(window => window.type === 'chart')
            .map(window => ({
              id: window.id,
              title: window.title,
              instrumentName: window.instrument.name,
            }))}
          onEdit={() => onEditWindow(item.id)}
          onPopOut={() => onPopOutWindow(item.id)}
          onDock={() => onDockWindow(item.id)}
          derived={item.mode === 'attached'}
          memberSource={memberSource}
          onSortChange={sort => onSortList(item.id, sort)}
          onVisibleColumnsChange={columns => onListColumnsChange(item.id, columns)}
          onReferencedSymbolsChange={onReferencedSymbolsChange}
        />
      )
    }
    return (
      <ChartWindow
        windowState={item}
        theme={theme}
        focused={group.focusedWindowId === item.id}
        maximized={group.maximizedWindowId === item.id}
        removable={group.windows.length > 1}
        poppedOutHost={poppedOutHost}
        onFocus={() => onFocusWindow(item.id)}
        onToggleMaximize={() => onToggleMaximize(item.id)}
        onRemove={() => onRemoveWindow(item.id)}
        onEdit={() => onEditWindow(item.id)}
        onPopOut={() => onPopOutWindow(item.id)}
        onDock={() => onDockWindow(item.id)}
        onCoverageChange={(rows, first, last) => onCoverageChange(item.id, item.instrument.symbol, rows, first, last)}
        onVisibleRangeChange={value => onVisibleRangeChange(item.id, value)}
        onVolumeVisibleChange={visible => onVolumeVisibleChange(item.id, visible)}
        onIndicatorChange={indicator => onIndicatorChange(item.id, indicator)}
        onSettlementVisibleChange={visible => onSettlementVisibleChange(item.id, visible)}
        onOpenInterestVisibleChange={visible => onOpenInterestVisibleChange(item.id, visible)}
        onPaneRatiosChange={ratios => onPaneRatiosChange(item.id, ratios)}
        onToolbarCollapsedChange={collapsed => onToolbarCollapsedChange(item.id, collapsed)}
        onRiskRewardVisibleChange={visible => onRiskRewardVisibleChange(item.id, visible)}
        onScenarioTargetChange={target => onScenarioTargetChange(item.id, target)}
        onTradingSystemsChange={systems => onTradingSystemsChange(item.id, systems)}
        onTradingSystemRecalculate={(systemId, state, refreshData) => (
          onTradingSystemRecalculate(item.id, systemId, state, refreshData)
        )}
      />
    )
  }

  return (
    <div className={`${group.maximizedWindowId ? 'window-group maximized' : 'window-group'}${poppedOutHost ? ' popout-host' : ''}`} data-group-id={group.id}>
      {renderOnlyWindowId
        ? renderWindow(renderOnlyWindowId)
        : group.maximizedWindowId
        ? renderWindow(group.maximizedWindowId)
        : <SplitLayout layout={group.layout} renderWindow={renderWindow} onRatioCommit={onResizeSplit}/>
      }
    </div>
  )
}
