import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, BarChart3, Filter, FolderKanban, Gauge, LayoutGrid, MessageSquare, Palette, PanelRightClose, RefreshCw, Settings2 } from 'lucide-react'
import type { PriceMode, VisibleRange } from './chartTypes'
import { InstrumentEditor } from './InstrumentEditor'
import { CustomIndexManager } from './CustomIndexManager'
import { DailyNote } from './DailyNote'
import { IntradaySubscriptionCoordinator, sendIntradaySubscription } from './intradaySubscription'
import { logInfo, logWarning } from './eventLogger'
import { LayoutManager } from './LayoutManager'
import { MarketBoardBadge } from './MarketBoardBadge'
import { ScreenerWorkspace, type ScreenerTargetList } from './ScreenerWorkspace'
import type { ScreenerCandidate } from './screenerClient'
import { RuntimeEventBar } from './RuntimeEventBar'
import { subscribeDrawingStore } from './drawingStore'
import { applyTheme, loadTheme, persistTheme, themes, type ThemeDefinition } from './themeStore'
import { updateSplitRatio } from './layoutTree'
import { WindowGroup } from './WindowGroup'
import { dockNativeWindow, focusNativeWindow, popOutNativeWindow, readPopoutTarget } from './nativeWindowBridge'
import { createWorkspaceSync, type WorkspaceSync } from './workspaceSync'
import { buildWorkspaceContext, publishWorkspaceContext } from './workspaceContext'
import type { TradingSystemWindowStates } from './tradingSystems'
import { middleMovingAveragePeriod } from './chartData'
import { useWorkspaceTrendRecalculation } from './useWorkspaceTrendRecalculation'
import {
  chartRanges,
  appendInstrumentToManualList,
  deriveReferencedSymbols,
  loadWorkspace,
  saveWorkspace,
  type ChartWindowState,
  type ChartPaneRatios,
  type Instrument,
  type InstrumentListWindowState,
  type ListColumnKey,
  type WindowGroupState,
  type WorkspaceState,
  type WorkspaceWindowState,
} from './workspace'
import {
  applyListSelection,
  removeMissingCustomGroupReferences,
  removeWorkspaceWindow,
  replaceDetachedWindowInstruments,
  resolveActiveChart,
  samePaneRatios,
} from './workspaceMutations'

const nativeWindowKey = (groupId: string, windowId: string) => JSON.stringify([groupId, windowId])

function parseNativeWindowKey(value: string): { groupId: string; windowId: string } | undefined {
  try {
    const parsed = JSON.parse(value) as unknown
    return Array.isArray(parsed) && parsed.length === 2
      && typeof parsed[0] === 'string' && typeof parsed[1] === 'string'
      ? { groupId: parsed[0], windowId: parsed[1] }
      : undefined
  } catch {
    return undefined
  }
}

export function StockWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceState>(loadWorkspace)
  const popoutTarget = useMemo(readPopoutTarget, [])
  const isPopoutHost = Boolean(popoutTarget)
  const [nativeShellReady, setNativeShellReady] = useState(Boolean(window.pywebview?.api?.pop_out_window))
  const [theme, setTheme] = useState<ThemeDefinition>(loadTheme)
  const [chatOpen, setChatOpen] = useState(false)
  const [layoutManagerOpen, setLayoutManagerOpen] = useState(false)
  const [customIndexManagerOpen, setCustomIndexManagerOpen] = useState(false)
  const [screenerOpen, setScreenerOpen] = useState(false)
  const [instrumentEditor, setInstrumentEditor] = useState<{ windowId?: string; tab: 'instruments' | 'groups' }>()
  const [resolvedWindowSymbols, setResolvedWindowSymbols] = useState<Record<string, string[]>>({})
  const [drawingRevision, setDrawingRevision] = useState(0)
  const hostGroupId = popoutTarget?.groupId ?? workspace.activeGroupId
  const activeGroup = workspace.groups.find(group => group.id === hostGroupId) ?? workspace.groups[0]
  const focusedWindow = activeGroup.windows.find(item => item.id === activeGroup.focusedWindowId) ?? activeGroup.windows[0]
  const activeChart = resolveActiveChart(activeGroup, focusedWindow)
  const referencedSymbols = useMemo(
    () => deriveReferencedSymbols(activeGroup, resolvedWindowSymbols),
    [activeGroup, resolvedWindowSymbols],
  )
  const referencedSymbolsKey = referencedSymbols.join('|')
  const subscriptionCoordinatorRef = useRef<IntradaySubscriptionCoordinator | undefined>(undefined)
  const workspaceSyncRef = useRef<WorkspaceSync | undefined>(undefined)
  const remoteWorkspaceRef = useRef<string | undefined>(undefined)
  const nativeRequestedRef = useRef(new Set<string>())
  const geometryTimersRef = useRef(new Map<string, number>())
  const pendingGeometryRef = useRef(new Map<string, {
    groupId: string
    windowId: string
    geometry: Partial<NonNullable<WorkspaceWindowState['presentation']['geometry']>>
  }>())
  const workspaceRef = useRef(workspace)
  workspaceRef.current = workspace
  const workspaceContext = useMemo(
    () => buildWorkspaceContext(activeGroup, resolvedWindowSymbols),
    [activeGroup, resolvedWindowSymbols, drawingRevision],
  )

  useEffect(() => {
    if (isPopoutHost) return
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
  }, [isPopoutHost])

  useEffect(() => {
    const sync = createWorkspaceSync(incoming => {
      const serialized = JSON.stringify(incoming)
      remoteWorkspaceRef.current = serialized
      setWorkspace(current => JSON.stringify(current) === serialized ? current : incoming)
    })
    workspaceSyncRef.current = sync
    return () => {
      sync.close()
      if (workspaceSyncRef.current === sync) workspaceSyncRef.current = undefined
    }
  }, [])

  useEffect(() => {
    const serialized = JSON.stringify(workspace)
    saveWorkspace(workspace)
    if (remoteWorkspaceRef.current === serialized) {
      remoteWorkspaceRef.current = undefined
      return
    }
    workspaceSyncRef.current?.publish(workspace)
  }, [workspace])
  useEffect(() => applyTheme(theme), [theme])

  useEffect(() => {
    const ready = () => setNativeShellReady(true)
    window.addEventListener('pywebviewready', ready)
    return () => window.removeEventListener('pywebviewready', ready)
  }, [])

  useEffect(() => {
    if (isPopoutHost) return
    const controller = new AbortController()
    const reconcile = async () => {
      try {
        const response = await fetch('/api/custom-groups', { signal: controller.signal })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json() as { items?: Array<{ symbol?: string }> }
        const existingSymbols = new Set(
          (payload.items ?? []).map(item => item.symbol).filter((symbol): symbol is string => Boolean(symbol)),
        )
        setWorkspace(current => removeMissingCustomGroupReferences(current, existingSymbols))
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          logWarning('workspace', '自选集合引用校验失败，保留现有窗口状态', { error })
        }
      }
    }
    void reconcile()
    const handleGroupChange = (event: Event) => {
      const detail = (event as CustomEvent<{ symbol?: string; deleted?: boolean }>).detail
      if (!detail?.deleted || !detail.symbol) return
      void reconcile()
    }
    window.addEventListener('stock-harness:custom-groups-changed', handleGroupChange)
    return () => {
      controller.abort()
      window.removeEventListener('stock-harness:custom-groups-changed', handleGroupChange)
    }
  }, [isPopoutHost])

  useEffect(() => subscribeDrawingStore(() => setDrawingRevision(value => value + 1)), [])

  useEffect(() => {
    if (isPopoutHost) return
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
  }, [isPopoutHost, workspaceContext])

  useEffect(() => {
    if (isPopoutHost) return
    subscriptionCoordinatorRef.current?.update({ groupId: activeGroup.id, symbols: referencedSymbols })
  }, [activeGroup.id, isPopoutHost, referencedSymbolsKey])

  const updateActiveGroup = useCallback((update: (group: WindowGroupState) => WindowGroupState) => {
    setWorkspace(current => ({
      ...current,
      groups: current.groups.map(group => group.id === hostGroupId ? update(group) : group),
    }))
  }, [hostGroupId])

  const updateWindow = useCallback((id: string, update: (item: WorkspaceWindowState) => WorkspaceWindowState) => {
    updateActiveGroup(group => ({
      ...group,
      windows: group.windows.map(item => item.id === id ? update(item) : item),
    }))
  }, [updateActiveGroup])

  const updateWindowPresentation = useCallback((
    groupId: string,
    id: string,
    update: (presentation: WorkspaceWindowState['presentation']) => WorkspaceWindowState['presentation'],
  ) => {
    setWorkspace(current => ({
      ...current,
      groups: current.groups.map(group => group.id !== groupId ? group : ({
        ...group,
        windows: group.windows.map(item => item.id === id
          ? { ...item, presentation: update(item.presentation) } as WorkspaceWindowState
          : item),
      })),
    }))
  }, [])

  useEffect(() => {
    if (isPopoutHost) return
    const handleClosed = (event: Event) => {
      const detail = (event as CustomEvent<{ groupId?: string; windowId?: string }>).detail
      if (!detail?.groupId || !detail.windowId) return
      nativeRequestedRef.current.delete(nativeWindowKey(detail.groupId, detail.windowId))
      updateWindowPresentation(detail.groupId, detail.windowId, current => ({
        ...current, mode: 'docked',
      }))
    }
    const handleGeometry = (event: Event) => {
      const detail = (event as CustomEvent<{
        groupId?: string
        windowId?: string
        geometry?: Partial<NonNullable<WorkspaceWindowState['presentation']['geometry']>>
      }>).detail
      if (!detail?.groupId || !detail.windowId || !detail.geometry) return
      const key = nativeWindowKey(detail.groupId, detail.windowId)
      const pending = pendingGeometryRef.current.get(key)
      pendingGeometryRef.current.set(key, {
        groupId: detail.groupId,
        windowId: detail.windowId,
        geometry: { ...pending?.geometry, ...detail.geometry },
      })
      const timer = geometryTimersRef.current.get(key)
      if (timer !== undefined) window.clearTimeout(timer)
      geometryTimersRef.current.set(key, window.setTimeout(() => {
        const next = pendingGeometryRef.current.get(key)
        pendingGeometryRef.current.delete(key)
        geometryTimersRef.current.delete(key)
        if (!next) return
        updateWindowPresentation(next.groupId, next.windowId, current => ({
          ...current,
          geometry: {
            width: next.geometry.width ?? current.geometry?.width ?? 1200,
            height: next.geometry.height ?? current.geometry?.height ?? 760,
            ...(next.geometry.x !== undefined || current.geometry?.x !== undefined
              ? { x: next.geometry.x ?? current.geometry?.x }
              : {}),
            ...(next.geometry.y !== undefined || current.geometry?.y !== undefined
              ? { y: next.geometry.y ?? current.geometry?.y }
              : {}),
          },
        }))
      }, 150))
    }
    window.addEventListener('stock-harness:native-window-closed', handleClosed)
    window.addEventListener('stock-harness:native-window-geometry', handleGeometry)
    return () => {
      window.removeEventListener('stock-harness:native-window-closed', handleClosed)
      window.removeEventListener('stock-harness:native-window-geometry', handleGeometry)
      geometryTimersRef.current.forEach(timer => window.clearTimeout(timer))
      geometryTimersRef.current.clear()
      pendingGeometryRef.current.clear()
    }
  }, [isPopoutHost, updateWindowPresentation])

  const popOutWindow = useCallback(async (id: string) => {
    const item = activeGroup.windows.find(window => window.id === id)
    if (!item) return
    const target = { groupId: activeGroup.id, windowId: id }
    const key = nativeWindowKey(target.groupId, target.windowId)
    updateActiveGroup(group => ({
      ...group,
      maximizedWindowId: group.maximizedWindowId === id ? undefined : group.maximizedWindowId,
      windows: group.windows.map(window => window.id === id
        ? { ...window, presentation: { ...window.presentation, mode: 'popped-out' } } as WorkspaceWindowState
        : window),
    }))
    nativeRequestedRef.current.add(key)
    const result = await popOutNativeWindow(
      target,
      `StockHarness - ${item.type === 'chart' ? item.instrument.name : item.title}`,
      item.presentation.geometry,
    )
    if (!result.ok) {
      nativeRequestedRef.current.delete(key)
      updateWindowPresentation(activeGroup.id, id, current => ({ ...current, mode: 'docked' }))
      logWarning('desktop-window', '弹出独立窗口失败', { state: result.state, limit: result.limit })
    }
  }, [activeGroup, updateActiveGroup, updateWindowPresentation])

  const dockWindow = useCallback(async (id: string) => {
    const target = { groupId: activeGroup.id, windowId: id }
    nativeRequestedRef.current.delete(nativeWindowKey(target.groupId, target.windowId))
    updateWindowPresentation(activeGroup.id, id, current => ({ ...current, mode: 'docked' }))
    const result = await dockNativeWindow(target)
    if (!result.ok) logWarning('desktop-window', '恢复独立窗口失败', { state: result.state })
  }, [activeGroup.id, updateWindowPresentation])

  const focusPopoutWindow = useCallback(async (id: string) => {
    const result = await focusNativeWindow({ groupId: activeGroup.id, windowId: id })
    if (!result.ok) logWarning('desktop-window', '已弹出窗口不可用', { state: result.state })
  }, [activeGroup.id])

  const popoutPresentationKey = useMemo(() => JSON.stringify(workspace.groups.flatMap(group => (
    group.windows.map(item => [group.id, item.id, item.presentation.mode])
  ))), [workspace.groups])

  useEffect(() => {
    if (isPopoutHost || !nativeShellReady) return
    const desired = new Set<string>()
    workspaceRef.current.groups.forEach(group => group.windows.forEach(item => {
      const key = nativeWindowKey(group.id, item.id)
      if (item.presentation.mode !== 'popped-out') return
      desired.add(key)
      if (nativeRequestedRef.current.has(key)) return
      nativeRequestedRef.current.add(key)
      void popOutNativeWindow(
        { groupId: group.id, windowId: item.id },
        `StockHarness - ${item.type === 'chart' ? item.instrument.name : item.title}`,
        item.presentation.geometry,
      ).then(result => {
        if (result.ok) return
        nativeRequestedRef.current.delete(key)
        updateWindowPresentation(group.id, item.id, current => ({ ...current, mode: 'docked' }))
        logWarning('desktop-window', '恢复已弹出窗口失败', { state: result.state, limit: result.limit })
      })
    }))
    nativeRequestedRef.current.forEach(key => {
      if (desired.has(key)) return
      nativeRequestedRef.current.delete(key)
      const target = parseNativeWindowKey(key)
      if (target) void dockNativeWindow(target)
    })
  }, [isPopoutHost, nativeShellReady, popoutPresentationKey, updateWindowPresentation])

  const updateReferencedSymbols = useCallback((id: string, symbols: string[]) => {
    setResolvedWindowSymbols(current => {
      const normalized = [...new Set(symbols)].sort()
      if ((current[id] ?? []).join('|') === normalized.join('|')) return current
      return { ...current, [id]: normalized }
    })
  }, [])

  const removeWindow = (id: string) => {
    updateActiveGroup(group => removeWorkspaceWindow(group, id))
  }

  const selectListInstrument = useCallback((id: string, instrument: Instrument) => {
    updateActiveGroup(group => applyListSelection(group, id, instrument))
  }, [updateActiveGroup])

  const saveWindowInstruments = useCallback((id: string, instruments: Instrument[]) => {
    updateActiveGroup(group => replaceDetachedWindowInstruments(group, id, instruments))
  }, [updateActiveGroup])

  const screenerTargetLists = useMemo<ScreenerTargetList[]>(() => activeGroup.windows
    .filter((item): item is InstrumentListWindowState => (
      item.type === 'instrument-list' && item.mode === 'detached'
    ))
    .map(item => ({
      id: item.id,
      title: item.title,
      instrumentCount: item.content.instruments.length,
    })), [activeGroup.windows])

  const addScreenerCandidateToList = useCallback((
    windowId: string,
    candidate: ScreenerCandidate,
  ): boolean => {
    const target = activeGroup.windows.find(item => item.id === windowId)
    if (target?.type !== 'instrument-list' || target.mode !== 'detached') return false
    if (target.content.instruments.some(item => item.symbol === candidate.symbol)) {
      logInfo('screener', '选股标的已存在于目标列表', {
        symbol: candidate.symbol, windowId,
      })
      return false
    }
    const instrument: Instrument = {
      symbol: candidate.symbol,
      name: candidate.name,
      kind: candidate.kind,
      exchange: candidate.exchange,
      category: '个股',
      rows: 0,
    }
    updateActiveGroup(group => appendInstrumentToManualList(
      group, windowId, instrument,
    ).group)
    logInfo('screener', '选股标的已添加到固定列表', {
      symbol: candidate.symbol, windowId,
    })
    return true
  }, [activeGroup.windows, updateActiveGroup])

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

  const handleSettlementVisible = useCallback((id: string, settlementVisible: boolean) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, settlementVisible } }
      : item)
  }, [updateWindow])

  const handleOpenInterestVisible = useCallback((id: string, openInterestVisible: boolean) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, openInterestVisible } }
      : item)
  }, [updateWindow])

  const handlePaneRatios = useCallback((id: string, paneRatios: ChartPaneRatios) => {
    updateWindow(id, item => item.type === 'chart'
      ? samePaneRatios(item.chart.paneRatios, paneRatios)
        ? item
        : { ...item, chart: { ...item.chart, paneRatios } }
      : item)
  }, [updateWindow])

  const handleToolbarCollapsed = useCallback((id: string, drawingToolbarCollapsed: boolean) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, drawingToolbarCollapsed } }
      : item)
  }, [updateWindow])

  const handleTradingSystems = useCallback((id: string, tradingSystems: TradingSystemWindowStates) => {
    updateWindow(id, item => item.type === 'chart'
      ? { ...item, chart: { ...item.chart, tradingSystems } }
      : item)
  }, [updateWindow])

  const handleTradingSystemRecalculate = useWorkspaceTrendRecalculation(
    activeGroup,
    updateWindow,
  )

  const updateActiveChart = (update: (chart: ChartWindowState) => ChartWindowState) => {
    if (!activeChart) return
    updateWindow(activeChart.id, item => item.type === 'chart' ? update(item) : item)
  }

  const setPriceMode = (priceMode: PriceMode) => {
    updateActiveChart(item => ({ ...item, chart: { ...item.chart, priceMode } }))
  }

  const renderActiveWindowGroup = (renderOnlyWindowId?: string) => <WindowGroup
    group={activeGroup}
    theme={theme}
    renderOnlyWindowId={renderOnlyWindowId}
    poppedOutHost={isPopoutHost}
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
    onSettlementVisibleChange={handleSettlementVisible}
    onOpenInterestVisibleChange={handleOpenInterestVisible}
    onPaneRatiosChange={handlePaneRatios}
    onToolbarCollapsedChange={handleToolbarCollapsed}
    onTradingSystemsChange={handleTradingSystems}
    onTradingSystemRecalculate={handleTradingSystemRecalculate}
    onReferencedSymbolsChange={updateReferencedSymbols}
    onPopOutWindow={popOutWindow}
    onDockWindow={dockWindow}
    onFocusPopoutWindow={focusPopoutWindow}
  />

  if (isPopoutHost && popoutTarget) {
    const targetExists = activeGroup.id === popoutTarget.groupId
      && activeGroup.windows.some(item => item.id === popoutTarget.windowId)
    return <main className="popout-workstation">
      {targetExists
        ? renderActiveWindowGroup(popoutTarget.windowId)
        : <div className="popout-missing">该窗口已从布局中移除。</div>}
      {instrumentEditor && <InstrumentEditor
        target={activeGroup.windows.find(item => item.id === instrumentEditor.windowId)}
        initialTab={instrumentEditor.tab}
        onSave={saveWindowInstruments}
        onClose={() => setInstrumentEditor(undefined)}
      />}
    </main>
  }

  if (layoutManagerOpen) {
    return <LayoutManager workspace={workspace} onChange={setWorkspace} onClose={() => setLayoutManagerOpen(false)}/>
  }
  if (screenerOpen) {
    return <ScreenerWorkspace
      theme={theme}
      onClose={() => setScreenerOpen(false)}
      targetLists={screenerTargetLists}
      onAddCandidateToList={addScreenerCandidateToList}
    />
  }

  return (
    <main className={chatOpen ? 'workstation' : 'workstation chat-closed'}>
      <section className="workspace">
        <header className="toolbar">
          <div className="workspace-identity">
            <span className="workspace-brand"><BarChart3 size={18}/><strong>StockHarness</strong></span>
            {activeChart && (
              <span className="security"><LayoutGrid size={16}/><span>
                <span className="instrument-name-line"><strong>{activeChart.instrument.name}</strong><MarketBoardBadge instrument={activeChart.instrument}/></span>
                <small>{activeChart.instrument.symbol} · {activeChart.instrument.category ?? activeChart.instrument.kind}</small>
              </span></span>
            )}
          </div>
          <div className="toolbar-actions">
            <button className="command-button" title="选股器" aria-label="选股器" onClick={() => setScreenerOpen(true)}><Filter size={15}/>选股器</button>
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
              {(activeChart.instrument.kind === 'futures-contract' || activeChart.instrument.kind === 'futures-continuous') && <>
                <button
                  className={activeChart.chart.settlementVisible ? 'active' : ''}
                  title={activeChart.chart.settlementVisible ? '隐藏结算价线' : '显示结算价线'}
                  aria-pressed={activeChart.chart.settlementVisible}
                  onClick={() => handleSettlementVisible(activeChart.id, !activeChart.chart.settlementVisible)}
                >结算</button>
                <button
                  className={activeChart.chart.openInterestVisible ? 'active' : ''}
                  title={activeChart.chart.openInterestVisible ? '隐藏持仓量' : '显示持仓量'}
                  aria-pressed={activeChart.chart.openInterestVisible}
                  onClick={() => handleOpenInterestVisible(activeChart.id, !activeChart.chart.openInterestVisible)}
                >持仓</button>
              </>}
            </div>
            <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA {middleMovingAveragePeriod(activeChart.instrument.symbol)}</span><span className="ma ma-long">MA 60</span>
          </>}
          <span className="window-count">{activeGroup.windows.length}/8</span>
        </div>
        {renderActiveWindowGroup()}
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
