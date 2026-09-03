import { useCallback, useEffect, useRef } from 'react'
import { logError, logInfo, logWarning } from './eventLogger'
import { refreshThenRecalculateTrend } from './trendRefreshCoordinator'
import { recalculateTrendAnalysis } from './trendAnalysisClient'
import { beginTrendRequest, isCurrentTrendRequest } from './trendRequestGuard'
import {
  normalizeTrendTradingSystemSettings,
  type TradingSystemWindowState,
} from './tradingSystems'
import type { WindowGroupState, WorkspaceWindowState } from './workspace'

type UpdateWindow = (
  id: string,
  update: (item: WorkspaceWindowState) => WorkspaceWindowState,
) => void

export function useWorkspaceTrendRecalculation(
  activeGroup: WindowGroupState,
  updateWindow: UpdateWindow,
) {
  const activeGroupRef = useRef(activeGroup)
  const requestGenerationsRef = useRef(new Map<string, number>())

  useEffect(() => {
    activeGroupRef.current = activeGroup
  }, [activeGroup])

  return useCallback((
    id: string,
    systemId: string,
    system: TradingSystemWindowState,
    refreshData: boolean,
  ): Promise<void> => {
    const item = activeGroup.windows.find(window => window.id === id)
    if (item?.type !== 'chart') return Promise.resolve()
    window.dispatchEvent(new CustomEvent('stock-harness:trading-system-recalculate', {
      detail: { windowId: id, systemId, symbol: item.instrument.symbol, refreshData },
    }))
    logInfo('trading-system', '交易体系测算请求已派发', {
      windowId: id,
      systemId,
      symbol: item.instrument.symbol,
      refreshData,
    })
    if (systemId !== 'trend') return Promise.resolve()
    const requestToken = beginTrendRequest(requestGenerationsRef.current, {
      groupId: activeGroup.id,
      windowId: id,
      symbol: item.instrument.symbol,
    })
    const requestIsCurrent = () => {
      const group = activeGroupRef.current
      const activeWindow = group.windows.find(window => window.id === id)
      return isCurrentTrendRequest(
        requestGenerationsRef.current,
        requestToken,
        activeWindow?.type === 'chart'
          ? {
            groupId: group.id,
            windowId: id,
            symbol: activeWindow.instrument.symbol,
          }
          : undefined,
      )
    }
    const settings = normalizeTrendTradingSystemSettings(system.settings)
    const calculation = refreshData
      ? refreshThenRecalculateTrend(
        item.instrument.symbol,
        settings,
        system.settingsRevision,
        {
          onRefresh: result => {
            if (!requestIsCurrent()) return
            window.dispatchEvent(new CustomEvent('stock-harness:latest-daily-refreshed', {
              detail: { symbol: item.instrument.symbol, ...result },
            }))
            if (result.warning) {
              logWarning(
                'trading-system',
                '更新测算的数据刷新完成但存在警告，将使用最后可用数据',
                {
                  windowId: id,
                  symbol: item.instrument.symbol,
                  mode: result.mode,
                  state: result.status,
                  error: result.error,
                },
              )
            } else {
              logInfo('trading-system', '更新测算的数据刷新完成', {
                windowId: id,
                symbol: item.instrument.symbol,
                mode: result.mode,
                state: result.status,
              })
            }
          },
          onRefreshError: error => {
            if (!requestIsCurrent()) return
            logWarning(
              'trading-system',
              '更新测算的数据刷新失败，将使用最后可用数据继续分析',
              {
                windowId: id,
                symbol: item.instrument.symbol,
                error: error instanceof Error ? error.message : String(error),
              },
            )
          },
        },
      )
      : recalculateTrendAnalysis(
        item.instrument.symbol,
        settings,
        system.settingsRevision,
      )
    return calculation.then(() => {
      if (!requestIsCurrent()) return
      updateWindow(id, current => (
        current.type === 'chart'
        && current.instrument.symbol === requestToken.symbol
          ? {
            ...current,
            chart: {
              ...current.chart,
              tradingSystems: {
                ...current.chart.tradingSystems,
                trend: {
                  ...current.chart.tradingSystems.trend,
                  analysisStatus: 'current',
                },
              },
            },
          }
          : current
      ))
      logInfo('trading-system', '趋势交易体系测算完成', {
        windowId: id,
        symbol: item.instrument.symbol,
      })
      window.dispatchEvent(new CustomEvent('stock-harness:trend-analysis-updated', {
        detail: { windowId: id, symbol: item.instrument.symbol },
      }))
    }).catch(error => {
      if (!requestIsCurrent()) return
      updateWindow(id, current => (
        current.type === 'chart'
        && current.instrument.symbol === requestToken.symbol
          ? {
            ...current,
            chart: {
              ...current.chart,
              tradingSystems: {
                ...current.chart.tradingSystems,
                trend: {
                  ...current.chart.tradingSystems.trend,
                  analysisStatus: 'stale',
                },
              },
            },
          }
          : current
      ))
      logError('trading-system', '趋势交易体系测算失败', {
        windowId: id,
        symbol: item.instrument.symbol,
        error: error instanceof Error ? error.message : String(error),
      })
      throw error
    })
  }, [activeGroup, updateWindow])
}
