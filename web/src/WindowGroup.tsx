import type { VisibleRange } from './ChartCanvas'
import { InstrumentWindow } from './InstrumentWindow'
import type { WindowGroupState } from './workspace'

type WindowGroupProps = {
  group: WindowGroupState
  onFocusWindow: (id: string) => void
  onToggleMaximize: (id: string) => void
  onRemoveWindow: (id: string) => void
  onCoverageChange: (id: string, symbol: string, rows: number, first?: string, last?: string) => void
  onVisibleRangeChange: (id: string, value: VisibleRange) => void
}

export function WindowGroup({
  group,
  onFocusWindow,
  onToggleMaximize,
  onRemoveWindow,
  onCoverageChange,
  onVisibleRangeChange,
}: WindowGroupProps) {
  const visibleWindows = group.maximizedWindowId
    ? group.windows.filter(item => item.id === group.maximizedWindowId)
    : group.windows

  return (
    <div className={`window-group window-count-${visibleWindows.length}`} data-group-id={group.id}>
      {visibleWindows.map(item => (
        <InstrumentWindow
          key={item.id}
          windowState={item}
          focused={group.focusedWindowId === item.id}
          maximized={group.maximizedWindowId === item.id}
          removable={group.windows.length > 1}
          onFocus={() => onFocusWindow(item.id)}
          onToggleMaximize={() => onToggleMaximize(item.id)}
          onRemove={() => onRemoveWindow(item.id)}
          onCoverageChange={(rows, first, last) => onCoverageChange(item.id, item.instrument.symbol, rows, first, last)}
          onVisibleRangeChange={value => onVisibleRangeChange(item.id, value)}
        />
      ))}
    </div>
  )
}
