import { useCallback, useEffect, useState } from 'react'
import { BarChart3, FolderKanban, LayoutGrid, MessageSquare, PanelRightClose, RefreshCw, Settings2 } from 'lucide-react'
import type { PriceMode, VisibleRange } from './ChartCanvas'
import { InstrumentEditor } from './InstrumentEditor'
import { LayoutManager } from './LayoutManager'
import { removeLayoutWindow, updateSplitRatio } from './layoutTree'
import { WindowGroup } from './WindowGroup'
import { removeWindowAttachments } from './windowAttachments'
import {
  chartRanges,
  loadWorkspace,
  saveWorkspace,
  type ChartWindowState,
  type Instrument,
  type InstrumentListWindowState,
  type WindowGroupState,
  type WorkspaceState,
  type WorkspaceWindowState,
} from './workspace'

export function StockWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceState>(loadWorkspace)
  const [chatOpen, setChatOpen] = useState(true)
  const [layoutManagerOpen, setLayoutManagerOpen] = useState(false)
  const [instrumentEditor, setInstrumentEditor] = useState<{ windowId?: string; tab: 'instruments' | 'groups' }>()
  const activeGroup = workspace.groups.find(group => group.id === workspace.activeGroupId) ?? workspace.groups[0]
  const focusedWindow = activeGroup.windows.find(item => item.id === activeGroup.focusedWindowId) ?? activeGroup.windows[0]
  const activeChart = resolveActiveChart(activeGroup, focusedWindow)

  useEffect(() => saveWorkspace(workspace), [workspace])

  const updateActiveGroup = useCallback((update: (group: WindowGroupState) => WindowGroupState) => {
    setWorkspace(current => ({
      ...current,
      groups: current.groups.map(group => group.id === current.activeGroupId ? update(group) : group),
    }))
  }, [])

  const updateWindow = useCallback((id: string, update: (item: WorkspaceWindowState) => WorkspaceWindowState) => {
    updateActiveGroup(group => ({
      ...group,
      windows: group.windows.map(item => item.id === id ? update(item) : item),
    }))
  }, [updateActiveGroup])

  const removeWindow = (id: string) => {
    if (activeGroup.windows.length === 1) return
    updateActiveGroup(group => {
      const index = group.windows.findIndex(item => item.id === id)
      const windows = group.windows.filter(item => item.id !== id)
      const layout = removeLayoutWindow(group.layout, id)
      if (!layout) return group
      return {
        ...group,
        layout,
        windows,
        attachments: removeWindowAttachments(group.attachments, id),
        focusedWindowId: group.focusedWindowId === id
          ? windows[Math.min(index, windows.length - 1)].id
          : group.focusedWindowId,
        maximizedWindowId: group.maximizedWindowId === id ? undefined : group.maximizedWindowId,
      }
    })
  }

  const selectListInstrument = useCallback((id: string, instrument: Instrument) => {
    updateActiveGroup(group => applyListSelection(group, id, instrument))
  }, [updateActiveGroup])

  const saveWindowInstruments = useCallback((id: string, instruments: Instrument[]) => {
    updateActiveGroup(group => {
      const source = group.windows.find(item => item.id === id)
      if (!source || source.mode !== 'detached') return group
      if (source.type === 'chart') {
        const instrument = instruments[0]
        if (!instrument || instrument.kind === 'custom-group') return group
        return {
          ...group,
          windows: group.windows.map(item => item.id === id
            ? { ...source, instrument, chart: { ...source.chart, visibleRange: undefined } }
            : item),
        }
      }
      const nextSelection = instruments.find(item => item.symbol === source.selectedSymbol) ?? instruments[0]
      const updated = {
        ...group,
        windows: group.windows.map(item => item.id === id ? {
          ...source,
          content: { ...source.content, instruments },
          selectedSymbol: nextSelection?.symbol,
        } : item),
      }
      return nextSelection ? applyListSelection(updated, id, nextSelection) : updated
    })
  }, [updateActiveGroup])

  const sortList = useCallback((id: string, sort: NonNullable<InstrumentListWindowState['sort']>) => {
    updateWindow(id, item => item.type === 'instrument-list' ? { ...item, sort } : item)
  }, [updateWindow])

  const handleCoverage = useCallback((id: string, symbol: string, rows: number, first?: string, last?: string) => {
    updateWindow(id, item => item.type !== 'chart' || item.instrument.symbol !== symbol ? item : ({
      ...item,
      instrument: { ...item.instrument, rows, first_trade_date: first, last_trade_date: last },
    }))
  }, [updateWindow])

  const handleVisibleRange = useCallback((id: string, value: VisibleRange) => {
    updateWindow(id, item => {
      if (item.type !== 'chart') return item
      return item.chart.visibleRange?.from === value.from && item.chart.visibleRange.to === value.to
        ? item
        : { ...item, chart: { ...item.chart, visibleRange: value } }
    })
  }, [updateWindow])

  const updateActiveChart = (update: (chart: ChartWindowState) => ChartWindowState) => {
    if (!activeChart) return
    updateWindow(activeChart.id, item => item.type === 'chart' ? update(item) : item)
  }

  const setPriceMode = (priceMode: PriceMode) => {
    updateActiveChart(item => ({ ...item, chart: { ...item.chart, priceMode } }))
  }

  if (layoutManagerOpen) {
    return <LayoutManager workspace={workspace} onChange={setWorkspace} onClose={() => setLayoutManagerOpen(false)}/>
  }

  return (
    <main className={chatOpen ? 'workstation' : 'workstation chat-closed'}>
      <section className="workspace">
        <header className="toolbar">
          <div className="workspace-identity">
            <span className="workspace-brand"><BarChart3 size={18}/><strong>StockHarness</strong></span>
            {activeChart && (
              <span className="security"><LayoutGrid size={16}/><span>
                <strong>{activeChart.instrument.name}</strong>
                <small>{activeChart.instrument.symbol} · {activeChart.instrument.category ?? activeChart.instrument.kind}</small>
              </span></span>
            )}
          </div>
          <div className="toolbar-actions">
            <select aria-label="切换窗口组" value={activeGroup.id} onChange={event => setWorkspace(current => ({ ...current, activeGroupId: event.target.value }))}>
              {workspace.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
            </select>
            {activeChart && (
              <div className="range-tabs" aria-label="时间范围">
                {chartRanges.map(range => (
                  <button
                    key={range}
                    className={activeChart.chart.range === range ? 'active' : ''}
                    onClick={() => updateActiveChart(item => ({
                      ...item,
                      chart: { ...item.chart, range, visibleRange: undefined },
                    }))}
                  >{range}</button>
                ))}
              </div>
            )}
            <button className="command-button layout-entry" title="布局管理" aria-label="布局管理" onClick={() => setLayoutManagerOpen(true)}><Settings2 size={15}/>布局管理</button>
            <button className="icon-button" title="标的与自选集合" aria-label="标的与自选集合" onClick={() => setInstrumentEditor({ tab: 'groups' })}><FolderKanban size={16}/></button>
            <button
              className="icon-button"
              title="刷新应用"
              aria-label="刷新应用"
              onClick={() => window.location.reload()}
            ><RefreshCw size={16}/></button>
            <button
              className="icon-button"
              title={chatOpen ? '收起对话栏' : '展开对话栏'}
              aria-label={chatOpen ? '收起对话栏' : '展开对话栏'}
              onClick={() => setChatOpen(!chatOpen)}
            >{chatOpen ? <PanelRightClose size={17}/> : <MessageSquare size={17}/>}</button>
          </div>
        </header>
        <div className="market-strip">
          <span>{activeGroup.name}</span>
          {activeChart && <><span>日线</span><span>不复权</span>
            <div className="coordinate-tabs" aria-label="价格坐标">
              <button className={activeChart.chart.priceMode === 'normal' ? 'active' : ''} onClick={() => setPriceMode('normal')}>普通</button>
              <button className={activeChart.chart.priceMode === 'log' ? 'active' : ''} onClick={() => setPriceMode('log')}>对数</button>
            </div>
            <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA 20</span><span className="ma ma-long">MA 60</span>
          </>}
          <span className="window-count">{activeGroup.windows.length}/8</span>
        </div>
        <WindowGroup
          group={activeGroup}
          onFocusWindow={id => updateActiveGroup(group => ({ ...group, focusedWindowId: id }))}
          onToggleMaximize={id => updateActiveGroup(group => ({
            ...group,
            focusedWindowId: id,
            maximizedWindowId: group.maximizedWindowId === id ? undefined : id,
          }))}
          onRemoveWindow={removeWindow}
          onResizeSplit={(id, ratio) => updateActiveGroup(group => ({
            ...group,
            layout: updateSplitRatio(group.layout, id, ratio),
          }))}
          onSelectListInstrument={selectListInstrument}
          onEditWindow={id => setInstrumentEditor({ windowId: id, tab: 'instruments' })}
          onSortList={sortList}
          onCoverageChange={handleCoverage}
          onVisibleRangeChange={handleVisibleRange}
        />
        <footer className="statusbar">
          {activeChart && <>
            <span>{activeChart.instrument.first_trade_date ?? '—'} → {activeChart.instrument.last_trade_date ?? '—'}</span>
            <span>{activeChart.instrument.rows.toLocaleString()} 根日线</span>
          </>}
          <span className="sync-state"><i/>数据已同步</span>
        </footer>
      </section>

      {chatOpen && (
        <aside className="chat-panel">
          <header><MessageSquare size={16}/><strong>Chat</strong></header>
          <div className="chat-empty"><MessageSquare size={22}/></div>
          <div className="chat-input"><input disabled aria-label="消息"/><button disabled aria-label="发送">›</button></div>
        </aside>
      )}
      {instrumentEditor && <InstrumentEditor
        target={activeGroup.windows.find(item => item.id === instrumentEditor.windowId)}
        initialTab={instrumentEditor.tab}
        onSave={saveWindowInstruments}
        onClose={() => setInstrumentEditor(undefined)}
      />}
    </main>
  )
}

function resolveActiveChart(group: WindowGroupState, focused: WorkspaceWindowState): ChartWindowState | undefined {
  if (focused.type === 'chart') return focused
  const targetId = group.attachments.find(edge => edge.sourceWindowId === focused.id && edge.type === 'show-symbol')?.targetWindowId
  const target = group.windows.find(item => item.id === targetId)
  if (target?.type === 'chart') return target
  return group.windows.find((item): item is ChartWindowState => item.type === 'chart')
}

function applyListSelection(group: WindowGroupState, sourceId: string, instrument: Instrument): WindowGroupState {
  const edges = group.attachments.filter(attachment => attachment.sourceWindowId === sourceId)
  return {
    ...group,
    windows: group.windows.map(item => {
      if (item.id === sourceId && item.type === 'instrument-list') {
        return { ...item, selectedSymbol: instrument.symbol } satisfies InstrumentListWindowState
      }
      const symbolEdge = edges.find(edge => edge.targetWindowId === item.id && edge.type === 'show-symbol')
      if (symbolEdge && item.type === 'chart' && item.mode === 'attached' && instrument.kind !== 'custom-group') {
        return { ...item, instrument, chart: { ...item.chart, visibleRange: undefined } }
      }
      const membersEdge = edges.find(edge => edge.targetWindowId === item.id && edge.type === 'show-members')
      if (membersEdge && item.type === 'instrument-list' && item.mode === 'attached') {
        return { ...item, memberSourceWindowId: sourceId, selectedSymbol: undefined }
      }
      return item
    }),
  }
}
