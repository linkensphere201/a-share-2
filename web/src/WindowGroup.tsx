import type { ChartIndicator, VisibleRange } from './ChartCanvas'
import { ChartWindow } from './InstrumentWindow'
import { InstrumentListWindow } from './InstrumentListWindow'
import { SplitLayout } from './SplitLayout'
import type { ChartPaneRatios, Instrument, ListColumnKey, WindowGroupState } from './workspace'
import type { ThemeDefinition } from './themeStore'
import type { TradingSystemWindowStates } from './tradingSystems'

type WindowGroupProps = {
  group: WindowGroupState
  theme: ThemeDefinition
  onFocusWindow: (id: string) => void
  onToggleMaximize: (id: string) => void
  onRemoveWindow: (id: string) => void
  onResizeSplit: (id: string, ratio: number) => void
  onSelectListInstrument: (id: string, instrument: Instrument) => void
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
  onTradingSystemsChange: (id: string, systems: TradingSystemWindowStates) => void
  onTradingSystemRecalculate: (id: string, systemId: string) => void
  onReferencedSymbolsChange: (id: string, symbols: string[]) => void
}

export function WindowGroup({
  group,
  theme,
  onFocusWindow,
  onToggleMaximize,
  onRemoveWindow,
  onResizeSplit,
  onSelectListInstrument,
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
  onTradingSystemsChange,
  onTradingSystemRecalculate,
  onReferencedSymbolsChange,
}: WindowGroupProps) {
  const renderWindow = (windowId: string) => {
    const item = group.windows.find(window => window.id === windowId)
    if (!item) return null
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
          onFocus={() => onFocusWindow(item.id)}
          onToggleMaximize={() => onToggleMaximize(item.id)}
          onRemoveWindow={() => onRemoveWindow(item.id)}
          onSelect={instrument => onSelectListInstrument(item.id, instrument)}
          onEdit={() => onEditWindow(item.id)}
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
        onFocus={() => onFocusWindow(item.id)}
        onToggleMaximize={() => onToggleMaximize(item.id)}
        onRemove={() => onRemoveWindow(item.id)}
        onEdit={() => onEditWindow(item.id)}
        onCoverageChange={(rows, first, last) => onCoverageChange(item.id, item.instrument.symbol, rows, first, last)}
        onVisibleRangeChange={value => onVisibleRangeChange(item.id, value)}
        onVolumeVisibleChange={visible => onVolumeVisibleChange(item.id, visible)}
        onIndicatorChange={indicator => onIndicatorChange(item.id, indicator)}
        onSettlementVisibleChange={visible => onSettlementVisibleChange(item.id, visible)}
        onOpenInterestVisibleChange={visible => onOpenInterestVisibleChange(item.id, visible)}
        onPaneRatiosChange={ratios => onPaneRatiosChange(item.id, ratios)}
        onTradingSystemsChange={systems => onTradingSystemsChange(item.id, systems)}
        onTradingSystemRecalculate={systemId => onTradingSystemRecalculate(item.id, systemId)}
      />
    )
  }

  return (
    <div className={group.maximizedWindowId ? 'window-group maximized' : 'window-group'} data-group-id={group.id}>
      {group.maximizedWindowId
        ? renderWindow(group.maximizedWindowId)
        : <SplitLayout layout={group.layout} renderWindow={renderWindow} onRatioCommit={onResizeSplit}/>
      }
    </div>
  )
}
