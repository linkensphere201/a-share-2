import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, BarChart3, FolderKanban, Gauge, LayoutGrid, MessageSquare, Palette, PanelRightClose, RefreshCw, Settings2 } from 'lucide-react'
import type { PriceMode, VisibleRange } from './ChartCanvas'
import { InstrumentEditor } from './InstrumentEditor'
import { CustomIndexManager } from './CustomIndexManager'
import { DailyNote } from './DailyNote'
import { IntradaySubscriptionCoordinator, sendIntradaySubscription } from './intradaySubscription'
import { logError, logInfo, logWarning } from './eventLogger'
import { LayoutManager } from './LayoutManager'
import { RuntimeEventBar } from './RuntimeEventBar'
import { subscribeDrawingStore } from './drawingStore'
import { applyTheme, loadTheme, persistTheme, themes, type ThemeDefinition } from './themeStore'
import { removeLayoutWindow, updateSplitRatio } from './layoutTree'
import { WindowGroup } from './WindowGroup'
import { removeWindowAttachments } from './windowAttachments'
import { buildWorkspaceContext, publishWorkspaceContext } from './workspaceContext'
import type { TradingSystemWindowStates } from './tradingSystems'
import { normalizeTrendTradingSystemSettings } from './tradingSystems'
import { refreshThenRecalculateTrend } from './trendRefreshCoordinator'
import { beginTrendRequest, isCurrentTrendRequest } from './trendRequestGuard'
import {
  chartRanges,
  deriveReferencedSymbols,
  loadWorkspace,
  saveWorkspace,
  type ChartWindowState,
  type Instrument,
  type InstrumentListWindowState,
  type ListColumnKey,
  type WindowGroupState,
  type WorkspaceState,
  type WorkspaceWindowState,
} from './workspace'

export function StockWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceState>(loadWorkspace)
  const [theme, setTheme] = useState<ThemeDefinition>(loadTheme)
  const [chatOpen, setChatOpen] = useState(false)
  const [layoutManagerOpen, setLayoutManagerOpen] = useState(false)
  const [customIndexManagerOpen, setCustomIndexManagerOpen] = useState(false)
  const [instrumentEditor, setInstrumentEditor] = useState<{ windowId?: string; tab: 'instruments' | 'groups' }>()
  const [resolvedWindowSymbols, setResolvedWindowSymbols] = useState<Record<string, string[]>>({})
  const [drawingRevision, setDrawingRevision] = useState(0)
  const activeGroup = workspace.groups.find(group => group.id === workspace.activeGroupId) ?? workspace.groups[0]
  const focusedWindow = activeGroup.windows.find(item => item.id === activeGroup.focusedWindowId) ?? activeGroup.windows[0]
  const activeChart = resolveActiveChart(activeGroup, focusedWindow)
  const referencedSymbols = useMemo(
    () => deriveReferencedSymbols(activeGroup, resolvedWindowSymbols),
    [activeGroup, resolvedWindowSymbols],
  )
  const referencedSymbolsKey = referencedSymbols.join('|')
  const subscriptionCoordinatorRef = useRef<IntradaySubscriptionCoordinator | undefined>(undefined)
  const activeGroupRef = useRef(activeGroup)
  const trendRequestGenerationsRef = useRef(new Map<string, number>())
  const workspaceContext = useMemo(
    () => buildWorkspaceContext(activeGroup, resolvedWindowSymbols),
    [activeGroup, resolvedWindowSymbols, drawingRevision],
  )

  useEffect(() => {
    const coordinator = new IntradaySubscriptionCoordinator(
      sendIntradaySubscription,
      {
        onSuccess: (status, subscription) => logInfo('intraday', '活动窗体组行情订阅已更新', {
          group: subscription.groupId,
          symbols: status.symbol_count ?? subscription.symbols.length,
        }),
        onFailure: (error, failures, subscription) => {
          if (failures === 1 || failures % 6 === 0) {
            logWarning('intraday', '活动窗体组行情订阅失败，将自动重试', {
              group: subscription.groupId,
              error,
            })
          }
        },
      },
    )
    subscriptionCoordinatorRef.current = coordinator
    return () => {
      coordinator.dispose()
      if (subscriptionCoordinatorRef.current === coordinator) {
        subscriptionCoordinatorRef.current = undefined
      }
    }
  }, [])

  useEffect(() => saveWorkspace(workspace), [workspace])
  useEffect(() => applyTheme(theme), [theme])
  useEffect(() => { activeGroupRef.current = activeGroup }, [activeGroup])

  useEffect(() => subscribeDrawingStore(() => setDrawingRevision(value => value + 1)), [])

  useEffect(() => {
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      publishWorkspaceContext(workspaceContext, controller.signal).catch(error => {
        if ((error as Error).name !== 'AbortError') {
          logWarning('workspace-context', '活动工作区上下文发布失败', { error })
        }
      })
    }, 150)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [workspaceContext])

  useEffect(() => {
    subscriptionCoordinatorRef.current?.update({ groupId: activeGroup.id, symbols: referencedSymbols })
  }, [activeGroup.id, referencedSymbolsKey])

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

  const updateReferencedSymbols = useCallback((id: string, symbols: string[]) => {
    setResolvedWindowSymbols(current => {
      const normalized = [...new Set(symbols)].sort()
      if ((current[id] ?? []).join('|') === normalized.join('|')) return current
      return { ...current, [id]: normalized }
    })
  }, [])

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
      const selectable = instruments.filter(item => item.kind !== 'futures-product')
      if (source.type === 'chart') {
        const instrument = selectable[0]
        if (!instrument || instrument.kind === 'custom-group') return group
        return {
          ...group,
          windows: group.windows.map(item => item.id === id
            ? { ...source, instrument, chart: { ...source.chart, visibleRange: undefined } }
            : item),
        }
      }
      const nextSelection = selectable.find(item => item.symbol === source.selectedSymbol) ?? selectable[0]
      const updated = {
        ...group,
        windows: group.windows.map(item => item.id === id ? {
          ...source,
          content: { ...source.content, instruments: selectable },
          selectedSymbol: nextSelection?.symbol,
        } : item),
      }
      return nextSelection ? applyListSelection(updated, id, nextSelection) : updated
    })
  }, [updateActiveGroup])

  const sortList = useCallback((id: string, sort: NonNullable<InstrumentListWindowState['sort']>) => {
    updateWindow(id, item => item.type === 'instrument-list' ? { ...item, sort } : item)
  }, [updateWindow])

  const updateListColumns = useCallback((id: string, visibleColumns: ListColumnKey[]) => {
    updateWindow(id, item => item.type === 'instrument-list' ? { ...item, visibleColumns } : item)
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

  const handleVolumeVisible = useCallback((id: string, volumeVisible: boolean) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, volumeVisible } }
      : item)
  }, [updateWindow])

  const handleIndicator = useCallback((id: string, indicator: ChartWindowState['chart']['indicator']) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, indicator } }
      : item)
  }, [updateWindow])

  const handleTradingSystems = useCallback((id: string, tradingSystems: TradingSystemWindowStates) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, tradingSystems } }
      : item)
  }, [updateWindow])

  const handleTradingSystemRecalculate = useCallback((id: string, systemId: string) => {
    const item = activeGroup.windows.find(window => window.id === id)
    if (item?.type !== 'chart') return
    window.dispatchEvent(new CustomEvent('stock-harness:trading-system-recalculate', {
      detail: { windowId: id, systemId, symbol: item.instrument.symbol },
    }))
    logInfo('trading-system', '交易体系测算请求已派发', {
      windowId: id,
      systemId,
      symbol: item.instrument.symbol,
    })
    if (systemId !== 'trend') return
    const requestToken = beginTrendRequest(trendRequestGenerationsRef.current, {
      groupId: activeGroup.id,
      windowId: id,
      symbol: item.instrument.symbol,
    })
    const requestIsCurrent = () => {
      const group = activeGroupRef.current
      const activeWindow = group.windows.find(window => window.id === id)
      return isCurrentTrendRequest(
        trendRequestGenerationsRef.current,
        requestToken,
        activeWindow?.type === 'chart' ? {
          groupId: group.id,
          windowId: id,
          symbol: activeWindow.instrument.symbol,
        } : undefined,
      )
    }
    const system = item.chart.tradingSystems.trend
    void refreshThenRecalculateTrend(
      item.instrument.symbol,
      normalizeTrendTradingSystemSettings(system.settings),
      system.settingsRevision,
      {
        onRefresh: result => {
          if (!requestIsCurrent()) return
          window.dispatchEvent(new CustomEvent('stock-harness:latest-daily-refreshed', {
            detail: { symbol: item.instrument.symbol, ...result },
          }))
          if (result.warning) {
            logWarning('trading-system', '更新测算的数据刷新完成但存在警告，将使用最后可用数据', {
              windowId: id, symbol: item.instrument.symbol,
              mode: result.mode, state: result.status, error: result.error,
            })
          } else {
            logInfo('trading-system', '更新测算的数据刷新完成', {
              windowId: id, symbol: item.instrument.symbol,
              mode: result.mode, state: result.status,
            })
          }
        },
        onRefreshError: error => {
          if (!requestIsCurrent()) return
          logWarning('trading-system', '更新测算的数据刷新失败，将使用最后可用数据继续分析', {
            windowId: id, symbol: item.instrument.symbol,
            error: error instanceof Error ? error.message : String(error),
          })
        },
      },
    ).then(() => {
      if (!requestIsCurrent()) return
      updateWindow(id, window => window.type === 'chart'
        && window.instrument.symbol === requestToken.symbol ? {
        ...window,
        chart: {
          ...window.chart,
          tradingSystems: {
            ...window.chart.tradingSystems,
            trend: { ...window.chart.tradingSystems.trend, analysisStatus: 'current' },
          },
        },
      } : window)
      logInfo('trading-system', '趋势交易体系测算完成', {
        windowId: id, symbol: item.instrument.symbol,
      })
      window.dispatchEvent(new CustomEvent('stock-harness:trend-analysis-updated', {
        detail: { windowId: id, symbol: item.instrument.symbol },
      }))
    }).catch(error => {
      if (!requestIsCurrent()) return
      updateWindow(id, window => window.type === 'chart'
        && window.instrument.symbol === requestToken.symbol ? {
        ...window,
        chart: {
          ...window.chart,
          tradingSystems: {
            ...window.chart.tradingSystems,
            trend: { ...window.chart.tradingSystems.trend, analysisStatus: 'stale' },
          },
        },
      } : window)
      logError('trading-system', '趋势交易体系测算失败', {
        windowId: id, symbol: item.instrument.symbol,
        error: error instanceof Error ? error.message : String(error),
      })
    })
  }, [activeGroup, updateWindow])

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
            <select aria-label="切换窗口组" value={activeGroup.id} onChange={event => {
              const groupId = event.target.value
              logInfo('workspace', '切换活动窗体组', { from: activeGroup.id, to: groupId })
              setWorkspace(current => ({ ...current, activeGroupId: groupId }))
            }}>
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
            <label className="theme-picker" title="主题配色">
              <Palette size={14}/>
              <select aria-label="主题配色" value={theme.id} onChange={event => {
                const next = themes.find(item => item.id === event.target.value) ?? theme
                persistTheme(next)
                setTheme(next)
                logInfo('theme', '工作台主题已切换', { theme: next.id })
              }}>
                {themes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <button className="command-button layout-entry" title="布局管理" aria-label="布局管理" onClick={() => setLayoutManagerOpen(true)}><Settings2 size={15}/>布局管理</button>
            <button className="icon-button" title="自定义指数" aria-label="自定义指数" onClick={() => setCustomIndexManagerOpen(true)}><Gauge size={16}/></button>
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
            {activeChart.instrument.kind === 'custom-index' && <div className="coordinate-tabs" aria-label="指数图形">
              <button className={activeChart.chart.seriesMode === 'line' ? 'active' : ''} onClick={() => updateActiveChart(item => ({
                ...item, chart: { ...item.chart, seriesMode: 'line' },
              }))}>收盘线</button>
              <button className={activeChart.chart.seriesMode !== 'line' ? 'active' : ''} onClick={() => updateActiveChart(item => ({
                ...item, chart: { ...item.chart, seriesMode: 'candles' },
              }))}>合成K线</button>
            </div>}
            <div className="indicator-tabs" aria-label="图表分栏">
              <button
                className={activeChart.chart.volumeVisible ? 'active' : ''}
                title={activeChart.chart.volumeVisible ? '隐藏成交量' : '显示成交量'}
                aria-pressed={activeChart.chart.volumeVisible}
                onClick={() => handleVolumeVisible(activeChart.id, !activeChart.chart.volumeVisible)}
              ><BarChart3 size={12}/>成交量</button>
              <button
                className={activeChart.chart.indicator === 'macd' ? 'active' : ''}
                title={activeChart.chart.indicator === 'macd' ? '隐藏 MACD' : '显示 MACD'}
                aria-pressed={activeChart.chart.indicator === 'macd'}
                onClick={() => updateActiveChart(item => ({
                  ...item,
                  chart: { ...item.chart, indicator: item.chart.indicator === 'macd' ? 'none' : 'macd' },
                }))}
              ><Activity size={12}/>MACD</button>
            </div>
            <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA 20</span><span className="ma ma-long">MA 60</span>
          </>}
          <span className="window-count">{activeGroup.windows.length}/8</span>
        </div>
        <WindowGroup
          group={activeGroup}
          theme={theme}
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
          onListColumnsChange={updateListColumns}
          onCoverageChange={handleCoverage}
          onVisibleRangeChange={handleVisibleRange}
          onVolumeVisibleChange={handleVolumeVisible}
          onIndicatorChange={handleIndicator}
          onTradingSystemsChange={handleTradingSystems}
          onTradingSystemRecalculate={handleTradingSystemRecalculate}
          onReferencedSymbolsChange={updateReferencedSymbols}
        />
        <footer className="statusbar">
          <RuntimeEventBar/>
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
      <DailyNote/>
      {customIndexManagerOpen && <CustomIndexManager onClose={() => setCustomIndexManagerOpen(false)}/>}
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
