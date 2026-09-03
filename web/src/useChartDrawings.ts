import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteTrendLine,
  drawingIdentityKey,
  listDrawingMigrationCandidates,
  loadSymbolDrawings,
  resolveDrawingMigration,
  saveTrendLine,
  subscribeSymbolDrawings,
  type DrawingMigrationCandidate,
  type DrawingTarget,
  type TrendLineDrawing,
} from './drawingStore'
import { logInfo, logWarning } from './eventLogger'

type ChartDrawingIdentity = {
  symbol: string
  instrumentKind?: string
  priceBasis?: string | null
  ruleVersion?: string | null
}

type ChartDrawingOptions = ChartDrawingIdentity & {
  onTargetReset: () => void
}

export function useChartDrawings({
  symbol,
  instrumentKind,
  priceBasis,
  ruleVersion,
  onTargetReset,
}: ChartDrawingOptions) {
  const resetCallbackRef = useRef(onTargetReset)
  const [target, setTarget] = useState<DrawingTarget>(() => ({
    symbol,
    instrumentKind,
    priceBasis,
    ruleVersion,
  }))
  const targetKey = drawingIdentityKey(target)
  const [drawings, setDrawings] = useState<TrendLineDrawing[]>(() => (
    loadSymbolDrawings(target)
  ))
  const [migrationCandidates, setMigrationCandidates] = useState<
    DrawingMigrationCandidate[]
  >(() => listDrawingMigrationCandidates(target))

  useEffect(() => {
    resetCallbackRef.current = onTargetReset
  }, [onTargetReset])

  useEffect(() => {
    setTarget({ symbol, instrumentKind, priceBasis, ruleVersion })
  }, [instrumentKind, priceBasis, ruleVersion, symbol])

  useEffect(() => {
    const reload = () => {
      setDrawings(loadSymbolDrawings(target))
      setMigrationCandidates(listDrawingMigrationCandidates(target))
    }
    reload()
    resetCallbackRef.current()
    return subscribeSymbolDrawings(target, reload)
  }, [targetKey])

  const setIdentity = useCallback((identity: ChartDrawingIdentity) => {
    setTarget(identity)
  }, [])

  const resolveMigration = useCallback((
    candidateId: string,
    action: 'migrate' | 'reject',
  ) => {
    try {
      resolveDrawingMigration(target, candidateId, action)
      setDrawings(loadSymbolDrawings(target))
      setMigrationCandidates(listDrawingMigrationCandidates(target))
      logInfo(
        'drawing',
        action === 'migrate' ? '期货趋势线迁移完成' : '期货趋势线已保持隔离',
        { symbol: target.symbol, candidateId },
      )
    } catch (error) {
      logWarning('drawing', '期货趋势线迁移处理失败', {
        symbol: target.symbol,
        candidateId,
        action,
        error,
      })
    }
  }, [target])

  const updateDrawing = useCallback((
    id: string,
    update: (drawing: TrendLineDrawing) => TrendLineDrawing,
  ) => {
    const drawing = drawings.find(item => item.id === id)
    if (!drawing) return
    try {
      saveTrendLine({ ...update(drawing), updatedAt: new Date().toISOString() })
    } catch (error) {
      logWarning('drawing', '趋势线设置保存失败', {
        symbol: target.symbol,
        drawingId: id,
        error,
      })
    }
  }, [drawings, target.symbol])

  const updateDrawingStyle = useCallback((
    id: string,
    style: Partial<TrendLineDrawing['style']>,
  ) => {
    updateDrawing(id, drawing => ({
      ...drawing,
      style: { ...drawing.style, ...style },
    }))
  }, [updateDrawing])

  const toggleDrawingVisibility = useCallback((id: string) => {
    updateDrawing(id, drawing => ({ ...drawing, visible: !drawing.visible }))
  }, [updateDrawing])

  const removeDrawing = useCallback((id: string) => {
    deleteTrendLine(target, id)
    logInfo('drawing', '趋势线已删除', { symbol: target.symbol, drawingId: id })
  }, [target])

  return {
    target,
    targetKey,
    drawings,
    setDrawings,
    migrationCandidates,
    setIdentity,
    resolveMigration,
    updateDrawing,
    updateDrawingStyle,
    toggleDrawingVisibility,
    removeDrawing,
  }
}
